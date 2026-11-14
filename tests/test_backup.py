import os
import time
import zipfile
import pytest
from pathlib import Path
from main import ConfigBackupPlugin

class MockContext:
    def __init__(self, config_data):
        self.config_data = config_data
        self.notifications = []

    def get_config(self):
        return self.config_data

    def notify(self, payload):
        self.notifications.append(payload)

@pytest.fixture
def mock_hermes_home(tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    
    # Create valid files
    (hermes_home / "config.yaml").write_text("model: omniroute", encoding="utf-8")
    skills_dir = hermes_home / "skills" / "my_skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")
    
    # Create excluded files/dirs
    cache_dir = hermes_home / "cache"
    cache_dir.mkdir()
    (cache_dir / "temp.cache").write_text("cache data", encoding="utf-8")
    
    logs_dir = hermes_home / "logs"
    logs_dir.mkdir()
    (logs_dir / "app.log").write_text("log data", encoding="utf-8")

    sandboxes_dir = hermes_home / "sandboxes"
    sandboxes_dir.mkdir()
    (sandboxes_dir / "sandbox.py").write_text("print('sandbox')", encoding="utf-8")

    return hermes_home

def test_create_zip_backup(tmp_path, mock_hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(mock_hermes_home))
    dest_dir = tmp_path / "backups"
    
    plugin = ConfigBackupPlugin()
    backup_path = plugin.run_backup_sync(dest_dir, hermes_home=mock_hermes_home)
    
    assert backup_path.exists()
    assert backup_path.suffix == ".zip"
    
    # Check SHA256 sidecar
    checksum_path = dest_dir / f"{backup_path.name}.sha256"
    assert checksum_path.exists()
    assert len(checksum_path.read_text(encoding="utf-8")) > 0

    # Inspect zip contents
    with zipfile.ZipFile(backup_path, "r") as zipf:
        namelist = zipf.namelist()
        assert "config.yaml" in namelist
        assert "skills/my_skill/SKILL.md" in namelist
        
        # Verify exclusions
        for name in namelist:
            assert not name.startswith("cache")
            assert not name.startswith("logs")
            assert not name.startswith("sandboxes")

def test_get_existing_backups_and_rotation(tmp_path):
    dest_dir = tmp_path / "backups"
    dest_dir.mkdir()
    
    plugin = ConfigBackupPlugin()
    
    # Create mock backup files with staggered timestamps
    backups = []
    for i in range(5):
        b_path = dest_dir / f"hermes_config_backup_202611{i+1:02d}_120000.zip"
        b_path.write_text(f"dummy zip {i}", encoding="utf-8")
        sha_path = dest_dir / f"{b_path.name}.sha256"
        sha_path.write_text(f"abc123sha  {b_path.name}", encoding="utf-8")
        time.sleep(0.01) # ensure distinct mtime
        backups.append(b_path)

    existing = plugin._get_existing_backups(dest_dir)
    assert len(existing) == 5
    
    # Test rotation keeping max 2
    plugin._rotate_old_backups(dest_dir, max_keep=2)
    
    remaining = plugin._get_existing_backups(dest_dir)
    assert len(remaining) == 2
    
    # Verify sidecar for pruned is gone
    for old_b in backups[:-2]:
        assert not (dest_dir / f"{old_b.name}.sha256").exists()

def test_handle_action():
    plugin = ConfigBackupPlugin()
    res = plugin.handle_action("trigger_backup")
    assert res["status"] == "success"
    
    res_ignored = plugin.handle_action("unknown_action")
    assert res_ignored["status"] == "ignored"

def test_type_safe_settings_parsing():
    # Test malformed config values fall back gracefully
    ctx = MockContext({
        "plugins": {
            "config_backup": {
                "backup_enabled": "not_a_bool",
                "backup_interval_hours": "invalid_float",
                "max_backups_to_keep": "invalid_int",
                "backup_destination": "~/test_dest"
            }
        }
    })
    plugin = ConfigBackupPlugin(context=ctx)
    settings = plugin._get_settings()
    
    assert isinstance(settings["enabled"], bool)
    assert isinstance(settings["interval_hours"], float)
    assert isinstance(settings["max_keep"], int)
    assert settings["interval_hours"] == 24.0
    assert settings["max_keep"] == 30

def test_atomic_backup_no_temp_leftover(tmp_path, mock_hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(mock_hermes_home))
    dest_dir = tmp_path / "backups"
    
    plugin = ConfigBackupPlugin()
    backup_path = plugin.run_backup_sync(dest_dir, hermes_home=mock_hermes_home)

    assert backup_path.exists()
    # Check no .tmp files remain in dest_dir
    tmp_files = list(dest_dir.glob("*.tmp"))
    assert len(tmp_files) == 0

def test_checksum_verification_and_integrity_audit(tmp_path, mock_hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(mock_hermes_home))
    dest_dir = tmp_path / "backups"

    ctx = MockContext({"plugins": {"config_backup": {}}})
    plugin = ConfigBackupPlugin(context=ctx)
    backup_path = plugin.run_backup_sync(dest_dir, hermes_home=mock_hermes_home)

    assert plugin._verify_backup_checksum(backup_path) is True

    # Corrupt the archive after checksum generation.
    with open(backup_path, "ab") as f:
        f.write(b"corruption")

    assert plugin._verify_backup_checksum(backup_path) is False

    ctx.notifications.clear()
    plugin._audit_backups_integrity(dest_dir)
    assert any("integrity check failed" in payload["message"].lower() for payload in ctx.notifications)

def test_health_status_metrics(tmp_path, mock_hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(mock_hermes_home))
    dest_dir = tmp_path / "backups"

    ctx = MockContext({"plugins": {"config_backup": {"backup_destination": str(dest_dir)}}})
    plugin = ConfigBackupPlugin(context=ctx)
    plugin.run_backup_sync(dest_dir, hermes_home=mock_hermes_home)

    health = plugin.get_health_status()
    assert health["backup_count"] == 1
    assert health["total_size_bytes"] > 0
    assert health["integrity_verified_count"] == 1
    assert health["integrity_failed_count"] == 0
    assert health["latest_backup"] is not None

def test_restore_action_requires_explicit_confirmation(tmp_path, mock_hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(mock_hermes_home))
    dest_dir = tmp_path / "backups"

    ctx = MockContext({"plugins": {"config_backup": {"backup_destination": str(dest_dir)}}})
    plugin = ConfigBackupPlugin(context=ctx)
    plugin.run_backup_sync(dest_dir, hermes_home=mock_hermes_home)

    result = plugin.handle_action("restore_latest", {})
    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True

def test_restore_action_rejects_invalid_checksum(tmp_path, mock_hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(mock_hermes_home))
    dest_dir = tmp_path / "backups"

    ctx = MockContext({"plugins": {"config_backup": {"backup_destination": str(dest_dir)}}})
    plugin = ConfigBackupPlugin(context=ctx)
    backup_path = plugin.run_backup_sync(dest_dir, hermes_home=mock_hermes_home)
    with open(backup_path, "ab") as f:
        f.write(b"corruption")

    result = plugin.handle_action("restore_latest", {"confirm": True})
    assert result["status"] == "error"

def test_restore_latest_creates_safety_snapshot_and_restores(tmp_path, mock_hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(mock_hermes_home))
    dest_dir = tmp_path / "backups"

    ctx = MockContext({"plugins": {"config_backup": {"backup_destination": str(dest_dir)}}})
    plugin = ConfigBackupPlugin(context=ctx)
    plugin.run_backup_sync(dest_dir, hermes_home=mock_hermes_home)

    # Change active config before restoring.
    (mock_hermes_home / "config.yaml").write_text("changed: true", encoding="utf-8")

    result = plugin.handle_action("restore_latest", {"confirm": True})
    assert result["status"] == "success"
    assert (mock_hermes_home / "config.yaml").read_text(encoding="utf-8") == "model: omniroute"

    snapshots = list((dest_dir / "restore_snapshots").glob("hermes_config_backup_*.zip"))
    assert len(snapshots) == 1
    assert plugin._verify_backup_checksum(snapshots[0])

def test_health_status_action_returns_data(tmp_path, mock_hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(mock_hermes_home))
    dest_dir = tmp_path / "backups"

    ctx = MockContext({"plugins": {"config_backup": {"backup_destination": str(dest_dir)}}})
    plugin = ConfigBackupPlugin(context=ctx)
    plugin.run_backup_sync(dest_dir, hermes_home=mock_hermes_home)

    result = plugin.handle_action("health_status")
    assert result["status"] == "success"
    assert result["data"]["backup_count"] == 1

def test_size_anomaly_notification(tmp_path, mock_hermes_home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(mock_hermes_home))
    dest_dir = tmp_path / "backups"

    ctx = MockContext({"plugins": {"config_backup": {"backup_destination": str(dest_dir)}}})
    plugin = ConfigBackupPlugin(context=ctx)

    # First backup is small.
    plugin.run_backup_sync(dest_dir, hermes_home=mock_hermes_home)

    # Add a large file so the next backup differs by >= 20%.
    (mock_hermes_home / "big_file.txt").write_text("x" * (1024 * 1024), encoding="utf-8")

    ctx.notifications.clear()
    plugin._run_backup_and_verify()

    assert any("changed by 20%" in n["message"] for n in ctx.notifications)
