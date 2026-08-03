"""Temporary compatibility package for the existing Render relay service.

The Twitch Extension implementation moved to ``extensions.twitch.app`` during
the monorepo migration. Render keeps build and start commands in service
settings, so the production service still imports this historical package.
"""
