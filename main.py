import os
import time
import zipfile
import hashlib
import threading
from pathlib import Path
from loguru import logger

class ConfigBackupPlugin:
    def __init__(self, context=None):
        self.context = context
        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def initialize(self):
        logger.info("Initializing Config Backup Plugin v0.1.1 (Max Backups: 30, Checksums: Enabled)...")
        # Run startup check and creation in a background thread to prevent blocking gateway boot
        threading.Thread(target=self._startup_backup_and_check, daemon=True).start()
        self._start_timer_thread()

    def shutdown(self):
        logger.info("Shutting down Config Backup Plugin...")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def handle_action(self, action_id: str, payload=None):
        """Called by Hermes Desktop when a manual UI action button is clicked."""
        if action_id == "trigger_backup":
            logger.info("Manual backup triggered via Hermes Desktop UI button.")
            threading.Thread(target=self._run_backup_and_verify, daemon=True).start()
            return {"status": "success", "message": "Backup started successfully!"}
        return {"status": "ignored"}

    def _get_settings(self):
        if self.context and hasattr(self.context, "get_config"):
            cfg = self.context.get_config().get("plugins", {}).get("config_backup", {})
        else:
            cfg = {}
        
        try:
            interval = float(cfg.get("backup_interval_hours", 24))
        except (TypeError, ValueError):
            interval = 24.0

        try:
            max_keep = int(cfg.get("max_backups_to_keep", 30))
        except (TypeError, ValueError):
            max_keep = 30

        return {
            "enabled": bool(cfg.get("backup_enabled", True)),
            "interval_hours": max(interval, 0.1),
            "destination": os.path.expanduser(cfg.get("backup_destination", "~/hermes_backups")),
            "max_keep": max(max_keep, 1)
        }

    def _get_existing_backups(self, dest_dir: Path):
        if not dest_dir.exists():
            return []
        try:
            return sorted(dest_dir.glob("hermes_config_backup_*.zip"), key=os.path.getmtime, reverse=True)
        except Exception as e:
            logger.warning(f"Failed to list existing backups: {e}")
            return []

    def _create_zip_backup(self, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        final_backup_path = dest_dir / f"hermes_config_backup_{timestamp}.zip"
        tmp_backup_path = dest_dir / f"hermes_config_backup_{timestamp}.zip.tmp"
        
        excluded_dirs = {"cache", "logs", "sandboxes", "tmp", "node_modules"}

        file_count = 0
        total_uncompressed = 0

        try:
            with zipfile.ZipFile(tmp_backup_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                if hermes_home.exists():
                    for file_path in hermes_home.rglob("*"):
                        if file_path.is_file():
                            try:
                                relative_path = file_path.relative_to(hermes_home)
                            except ValueError:
                                continue
                            if any(part in excluded_dirs for part in relative_path.parts):
                                continue
                            try:
                                file_path.relative_to(dest_dir)
                                continue
                            except ValueError:
                                pass
                            
                            try:
                                file_size = file_path.stat().st_size
                                total_uncompressed += file_size
                                zipf.write(file_path, relative_path)
                                file_count += 1
                            except Exception as e:
                                logger.warning(f"Failed to include file {file_path}: {e}")

            compressed_size = tmp_backup_path.stat().st_size
            ratio = (compressed_size / total_uncompressed * 100) if total_uncompressed > 0 else 0
            
            # Atomic rename upon successful write
            tmp_backup_path.replace(final_backup_path)

            logger.info(
                f"Backup archive created: {final_backup_path.name} | "
                f"Files: {file_count} | Uncompressed: {total_uncompressed}B | "
                f"Compressed: {compressed_size}B ({ratio:.1f}% of original)"
            )

            # Generate SHA256 checksum sidecar file
            sha256_hash = hashlib.sha256()
            with open(final_backup_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            checksum = sha256_hash.hexdigest()
            
            checksum_path = dest_dir / f"{final_backup_path.name}.sha256"
            checksum_path.write_text(f"{checksum}  {final_backup_path.name}\n", encoding="utf-8")
            logger.debug(f"Generated SHA256 checksum sidecar: {checksum_path.name}")

            return final_backup_path

        except Exception as e:
            if tmp_backup_path.exists():
                try:
                    tmp_backup_path.unlink()
                except Exception:
                    pass
            logger.error(f"Failed to create zip backup: {e}")
            raise

    def _notify_user(self, message: str):
        """Sends a notification toast/popup to Hermes Desktop or logs to TUI gateway."""
        logger.warning(message)
        if self.context and hasattr(self.context, "notify"):
            try:
                self.context.notify({"kind": "warning", "message": message})
            except Exception as e:
                logger.error(f"Failed to dispatch desktop popup notification: {e}")

    def _run_backup_and_verify(self, is_startup=False):
        with self._lock:
            settings = self._get_settings()
            dest_dir = Path(settings["destination"])
            
            # 1. Gather existing backups before making a new one
            existing = self._get_existing_backups(dest_dir)
            
            # Calculate average size of the last up-to 3 backups if they exist safely
            last_three = existing[:3]
            avg_size = 0.0
            if last_three:
                sizes = []
                for b in last_three:
                    try:
                        if b.exists():
                            sizes.append(b.stat().st_size)
                    except Exception as e:
                        logger.warning(f"Could not stat backup file {b.name}: {e}")
                if sizes:
                    avg_size = sum(sizes) / len(sizes)

            # 2. Create the new backup
            try:
                logger.info("Creating new Hermes configuration backup...")
                new_backup_path = self._create_zip_backup(dest_dir)
                new_size = new_backup_path.stat().st_size
                logger.info(f"New backup verified: {new_backup_path.name} ({new_size} bytes)")

                # 3. Compare with average if we had previous backups to compare against
                if avg_size > 0:
                    diff_ratio = abs(new_size - avg_size) / avg_size
                    logger.info(f"Backup size comparison -> New: {new_size}B, Last 3 Avg: {avg_size:.1f}B, Variance: {diff_ratio * 100:.1f}%")

                    if diff_ratio >= 0.20:
                        warning_msg = (
                            "The size of the last Hermes config backup has changed by 20% or more. "
                            "You may want to check your config, skills, memories, etc. "
                            "If you've created a lot of skills or memories in your last few Hermes sessions, "
                            "you can probably ignore this."
                        )
                        self._notify_user(warning_msg)

                # 4. Prune old backups past max_keep
                self._rotate_old_backups(dest_dir, settings["max_keep"])

            except Exception as e:
                logger.error(f"Failed during backup generation or size comparison: {e}")

    def _startup_backup_and_check(self):
        time.sleep(3)
        settings = self._get_settings()
        if settings["enabled"]:
            self._run_backup_and_verify(is_startup=True)

    def _rotate_old_backups(self, dest_dir: Path, max_keep: int):
        if max_keep <= 0:
            return
        backups = self._get_existing_backups(dest_dir)
        if len(backups) > max_keep:
            for old_backup in backups[max_keep:]:
                try:
                    # Also remove corresponding .sha256 sidecar if present
                    sha_sidecar = dest_dir / f"{old_backup.name}.sha256"
                    if sha_sidecar.exists():
                        sha_sidecar.unlink()

                    old_backup.unlink()
                    logger.info(f"Pruned old backup: {old_backup.name}")
                except Exception as e:
                    logger.warning(f"Could not delete old backup {old_backup.name}: {e}")

    def _start_timer_thread(self):
        def loop():
            while not self._stop_event.is_set():
                settings = self._get_settings()
                interval_seconds = max(settings["interval_hours"], 0.1) * 3600
                if self._stop_event.wait(timeout=interval_seconds):
                    break
                if settings["enabled"]:
                    try:
                        self._run_backup_and_verify()
                    except Exception as e:
                        logger.error(f"Error in backup timer loop: {e}")

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    # Convenience method for testing
    def run_backup_sync(self, dest_dir: Path):
        """Synchronous runner for testing backup creation and verification."""
        settings = self._get_settings()
        settings["destination"] = str(dest_dir)
        return self._create_zip_backup(dest_dir)

plugin_class = ConfigBackupPlugin
