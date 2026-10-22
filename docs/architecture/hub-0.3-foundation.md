# Streamhouse Hub 0.3 foundation direction

Streamhouse Hub `0.2.0-alpha` is released and frozen at tag
`v0.2.0-alpha`. Active `develop` work targets `0.3.0-alpha` and begins with a
modular Hub architecture foundation. This is milestone direction, not a claim
that the capabilities below are already implemented.

The initial direction is intentionally narrow:

- define `ModuleRegistry`, `ModuleDescriptor`, and `ModuleContext` ownership;
- define an `ActionRegistry` for module-contributed actions;
- allow modules to contribute main tabs while retaining the current tab-based
  navigation model;
- establish optional docking infrastructure for live-use panels, especially
  platform chat and Broadcaster panels; and
- use OBS as the first module extraction and proof of concept.

This milestone does not introduce a marketplace, a general plugin loader, or
hot module unloading. Those remain outside the 0.3 foundation scope.
