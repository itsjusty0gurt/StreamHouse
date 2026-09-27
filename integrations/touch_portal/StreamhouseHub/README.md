# Streamhouse Hub Touch Portal plugin

This experimental same-PC plugin exposes one Touch Portal action: **Run
Streamhouse Routine**. It refreshes enabled routines from Hub every ten seconds
and sends the selected routine's stable ID back to Hub.

The installed plugin folder contains `entry.tp` and a one-file
`streamhouse_touch_portal.exe` built from `plugin.py`. The adapter uses Touch
Portal's newline-delimited local plugin socket on `127.0.0.1:12136`; Hub remains
the owner of routines, queues, and execution.

Run `integrations/touch_portal/build_plugin.ps1` from the repository to create
the importable `build/touch-portal/StreamhouseHub.tpp` package.
