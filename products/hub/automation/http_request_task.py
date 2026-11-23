"""Unauthenticated HTTP requests for Core Automation."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, MutableMapping
from math import isfinite
from urllib.parse import urlsplit

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest

from products.hub.automation.cancellation import current_cancellation
from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.variable_outputs import http_output_names
from products.hub.automation.variable_registry import PLACEHOLDER_PATTERN, VariableRegistry


HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MAX_HTTP_BYTES = 256 * 1024
_HTTP_HEADERS = {"accept": "Accept", "content-type": "Content-Type"}
_MEDIA_TYPE = re.compile(r"[A-Za-z0-9!#$&^_.+*-]+/[A-Za-z0-9!#$&^_.+*-]+(?:\s*;\s*charset=utf-8)?", re.I)


def _safe_url(value: str) -> tuple[str, str, int | None]:
    if len(value) > 4096 or any(c.isspace() or ord(c) < 32 for c in value) or any(c in value for c in "\\?#"):
        raise ValueError("Use a URL without a query string, fragment, backslash, or spaces.")
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
        if not host or parts.username is not None:
            raise ValueError
    except ValueError as error:
        raise ValueError("Enter a valid URL without embedded credentials.") from error
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            raise ValueError("Use a fixed hostname; Variables are supported in the path only.") from None
    if parts.scheme != "https" and not (
        parts.scheme == "http" and address is not None and address.is_loopback
    ):
        raise ValueError("Use HTTPS, or HTTP with a numeric loopback address for local testing.")
    return parts.scheme, host, port


def _header_value(value: str) -> bool:
    return len(value) <= 128 and bool(_MEDIA_TYPE.fullmatch(value))


def validate_http_config(config: Mapping[str, object]) -> None:
    if set(config) - {"url", "method", "headers", "body", "timeout_seconds", "result_prefix"}:
        raise ValueError("HTTP Request does not support credential fields or extra settings.")
    url = str(config.get("url", "")).strip()
    _safe_url(url)
    method = str(config.get("method", "GET")).upper()
    if method not in HTTP_METHODS:
        raise ValueError("Choose a supported HTTP method.")
    headers = config.get("headers", "")
    if not isinstance(headers, str) or len(headers) > 512:
        raise ValueError("Enter Accept or Content-Type headers, one per line.")
    names = set()
    for line in headers.splitlines():
        if not line.strip():
            continue
        name, colon, value = line.partition(":")
        key = name.strip().lower()
        if not colon or key not in _HTTP_HEADERS or key in names:
            raise ValueError("Only one Accept and one Content-Type header are allowed. Credentials are not supported.")
        if not (_header_value(value.strip()) or PLACEHOLDER_PATTERN.fullmatch(value.strip())):
            raise ValueError("Header values must be a media type (such as application/json) or one Variable.")
        names.add(key)
    body = config.get("body", "")
    if not isinstance(body, str) or (body.strip() and (
        method == "GET" or not PLACEHOLDER_PATTERN.fullmatch(body.strip())
    )):
        raise ValueError("Leave the body empty or use one Variable for a non-GET request. Do not save literal payloads.")
    try:
        timeout = float(config.get("timeout_seconds", 10))
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Enter a timeout from 1 to 60 seconds.") from error
    if not isfinite(timeout) or not 1 <= timeout <= 60:
        raise ValueError("Enter a timeout from 1 to 60 seconds.")
    http_output_names(config)


class _CancelBridge(QObject):
    requested = Signal()


class HttpRequestTask:
    task_type = "core.http_request"

    def __init__(self, variables: VariableRegistry) -> None:
        self.variables = variables

    def execute(self, task: TaskDefinition, trigger: TriggerEvent) -> TaskExecutionResult:
        def finish(status: int, body: str, detail: str, success: bool = False):
            if isinstance(trigger.context, MutableMapping):
                try:
                    names = http_output_names(task.config)
                except ValueError:
                    pass
                else:
                    trigger.context.update(zip(names, (str(status), body, "true" if success else "false", "" if success else detail)))
            return TaskExecutionResult(task.task_id, task.task_type, success, detail)

        cancellation = current_cancellation()
        if cancellation is not None and cancellation.cancelled:
            return finish(0, "", "HTTP request cancelled.")
        try:
            validate_http_config(task.config)
            authored_url = str(task.config["url"]).strip()
            url = self.variables.render(authored_url, trigger.context)
            if PLACEHOLDER_PATTERN.search(url) or _safe_url(url) != _safe_url(authored_url):
                return finish(0, "", "The URL did not resolve to the configured destination.")
            headers = {}
            for line in str(task.config.get("headers", "")).splitlines():
                if line.strip():
                    name, value = line.split(":", 1)
                    resolved = self.variables.render(value.strip(), trigger.context)
                    if not _header_value(resolved):
                        return finish(0, "", "A header Variable did not resolve to a supported media type.")
                    headers[_HTTP_HEADERS[name.strip().lower()]] = resolved
            body_template = str(task.config.get("body", "")).strip()
            payload = self.variables.render(body_template, trigger.context)
            if body_template and payload == body_template:
                return finish(0, "", "The request body Variable is unavailable.")
            data = payload.encode("utf-8")
            if len(data) > MAX_HTTP_BYTES:
                return finish(0, "", "Request body exceeds 256 KiB.")
            destination = QUrl(url, QUrl.ParsingMode.StrictMode)
            if not destination.isValid() or QCoreApplication.instance() is None:
                return finish(0, "", "HTTP request is unavailable or the URL is invalid.")
        except (ValueError, TypeError, KeyError, UnicodeError):
            return finish(0, "", "HTTP request configuration is invalid. Review the URL, headers, body, and timeout.")

        # Qt owns the asynchronous socket. The nested loop is the existing
        # synchronous-task bridge; it gives Qt back control while we wait.
        manager = QNetworkAccessManager()
        manager.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        request = QNetworkRequest(destination)
        attrs = QNetworkRequest.Attribute
        request.setAttribute(attrs.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        for attr in (attrs.CookieLoadControlAttribute, attrs.CookieSaveControlAttribute, attrs.AuthenticationReuseAttribute):
            request.setAttribute(attr, QNetworkRequest.LoadControl.Manual)
        for name, value in headers.items():
            request.setRawHeader(name.encode("ascii"), value.encode("ascii"))
        method = str(task.config.get("method", "GET")).upper()
        reply = manager.sendCustomRequest(request, method.encode("ascii"), data)
        reply.setReadBufferSize(MAX_HTTP_BYTES + 1)
        loop = QEventLoop()
        timer = QTimer(singleShot=True)
        bridge = _CancelBridge()
        buffer = bytearray()
        reason = ""

        def abort(message: str) -> None:
            nonlocal reason
            if not reason:
                reason = message
                if not reply.isFinished():
                    reply.abort()
                loop.quit()

        def read() -> None:
            if not reason:
                buffer.extend(bytes(reply.read(MAX_HTTP_BYTES + 1 - len(buffer))))
                if len(buffer) > MAX_HTTP_BYTES:
                    abort("Response exceeds 256 KiB.")

        reply.readyRead.connect(read)
        reply.finished.connect(loop.quit)
        timer.timeout.connect(lambda: abort("HTTP request timed out."))
        bridge.requested.connect(lambda: abort("HTTP request cancelled."), Qt.ConnectionType.QueuedConnection)
        disconnect = cancellation.add_callback(lambda _reason: bridge.requested.emit()) if cancellation else lambda: None
        try:
            timer.start(round(float(task.config.get("timeout_seconds", 10)) * 1000))
            if cancellation is not None and cancellation.cancelled:
                abort("HTTP request cancelled.")
            if not reason and not reply.isFinished():
                loop.exec()
            read()
            status = int(reply.attribute(attrs.HttpStatusCodeAttribute) or 0)
            if reason:
                return finish(status, "", reason)
            if 300 <= status < 400:
                return finish(status, "", f"HTTP {status}: redirects are not followed.")
            if status and not 200 <= status < 300:
                return finish(status, "", f"HTTP {status}: request failed.")
            if reply.error() != QNetworkReply.NetworkError.NoError or not status:
                return finish(status, "", "HTTP request failed to complete.")
            try:
                body = buffer.decode("utf-8-sig")
            except UnicodeError:
                return finish(status, "", "Response is not UTF-8 text.")
            return finish(status, body, f"HTTP {status}.", True)
        finally:
            disconnect()
            timer.stop()
            if not reply.isFinished():
                reply.abort()
            reply.deleteLater()
