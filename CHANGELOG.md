# Changelog

## 0.1.1

### Added

- Event-driven synchronization of the keyboard's volume indicator with desktop
  output volume, including changes made with the knob or desktop controls.
  Each update also syncs local date/time.
- A Settings tab with a saved, default-enabled in-app volume-sync preference,
  synchronization status, and the application version.
- An optional, default-off systemd user service for volume/time synchronization
  after the app closes and at graphical login. The service pauses while the app
  is open to prevent competing USB transfers.
