import os
import time
import zipfile
import pytest
from pathlib import Path
from main import ConfigBackupPlugin

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
    backup_path = plugin.run_backup_sync(dest_dir)
    
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
