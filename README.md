# Hermes Config Backup Plugin (`config_backup`)

An automated backup and monitoring plugin for **Hermes** and **Hermes Desktop**. This plugin compresses your core configuration files, memories, skills, and `.env` credentials into lightweight zip archives on a set timer, retains up to **30 archives**, generates cryptographic integrity hashes (`.sha256`), and alerts you if your backup size fluctuates abnormally.

## Features
- **Scheduled Backups:** Runs automatically on a configurable hour timer (default: every 24 hours).
- **Manual Trigger:** Includes a **"Backup Now"** button directly inside the Hermes Desktop UI panel.
- **SHA256 Integrity Verification:** Automatically generates `.sha256` checksum sidecar files for every backup archive to ensure bit-level integrity.
- **Compression Statistics & Logging:** Detailed logging of file count, uncompressed size, compressed size, and compression ratios.
- **Startup Size Anomaly Detection:** On launch, compares the newly generated backup against the average size of the last 3 archives. If the size shifts by **20% or more**, it triggers a desktop notification/TUI warning.
- **Smart Pruning:** Automatically retains the latest **30** backups (and their corresponding checksum sidecars) to balance safety with disk space economy.
- **Exclusion Guardrails:** Automatically skips heavy logs, temporary caching directories (`cache`, `logs`, `sandboxes`, `tmp`, `node_modules`), and active sandboxes.

## Installation
1. Clone or copy this plugin folder into your Hermes plugins directory:
   - Linux/macOS: `~/.hermes/plugins/config_backup/`
   - Windows: `%LOCALAPPDATA%\hermes\plugins\config_backup\`
2. Restart Hermes Desktop or your Hermes gateway.

## Configuration
You can customize the plugin settings in your global `~/.hermes/config.yaml` file:
```yaml
plugins:
  config_backup:
    backup_enabled: true
    backup_interval_hours: 24
    backup_destination: "~/hermes_backups"
    max_backups_to_keep: 30
```

## Running Tests
Run the pytest unit and integration test suite:
```powershell
pytest
```
