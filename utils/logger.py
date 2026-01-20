# -*- coding: utf-8 -*-
"""
Logging utilities for GeodbIO plugin.

Supports:
- File logging (always enabled)
- UI logging (when a callback is registered)
"""
import logging
import os
from typing import Optional, Callable


class UILogHandler(logging.Handler):
    """
    Custom logging handler that sends messages to the plugin UI.

    This handler calls a registered callback function with each log message,
    allowing the plugin dialog to display logs in the messagesTextBrowser.
    """

    def __init__(self, callback: Callable[[str, str], None]):
        """
        Initialize the UI handler.

        Args:
            callback: Function that takes (message, level) where level is
                     'info', 'warning', 'error', or 'debug'
        """
        super().__init__()
        self.callback = callback
        # Set a simple format for UI display (no timestamp - UI can add if needed)
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record):
        """Emit a log record to the UI."""
        try:
            # Map logging levels to UI levels
            level_map = {
                logging.DEBUG: 'info',  # Show debug as info in UI
                logging.INFO: 'info',
                logging.WARNING: 'warning',
                logging.ERROR: 'error',
                logging.CRITICAL: 'error',
            }
            ui_level = level_map.get(record.levelno, 'info')
            message = self.format(record)

            # Only show [CLAIMS DEBUG] messages and errors/warnings in UI
            # Filter out verbose debug messages to keep UI clean
            if record.levelno >= logging.WARNING or '[CLAIMS' in message:
                # Pass _from_logger=True to prevent feedback loop
                # (the callback is _log_message which would otherwise log back to us)
                self.callback(message, ui_level, _from_logger=True)
        except TypeError:
            # Fallback for callbacks that don't support _from_logger parameter
            try:
                self.callback(message, ui_level)
            except Exception:
                pass
        except Exception:
            # Don't let UI logging errors crash the plugin
            pass


class PluginLogger:
    """Custom logger for the plugin - supports file and UI logging."""

    _instance: Optional[logging.Logger] = None
    _ui_handler: Optional[UILogHandler] = None

    @classmethod
    def reset(cls):
        """Reset the logger state.

        Call this during plugin unload to ensure clean state on reload.
        This prevents stale handlers from being kept across plugin reloads.
        """
        cls.unregister_ui_handler()
        if cls._instance is not None:
            # Remove all handlers to prevent duplicates on reload
            for handler in cls._instance.handlers[:]:
                try:
                    handler.close()
                    cls._instance.removeHandler(handler)
                except Exception:
                    pass
            cls._instance = None

    @classmethod
    def get_logger(cls, name: str = 'GeodbIO') -> logging.Logger:
        """
        Get or create logger instance.

        Args:
            name: Logger name

        Returns:
            Logger instance
        """
        if cls._instance is None:
            cls._instance = cls._setup_logger(name)
        return cls._instance

    @classmethod
    def _setup_logger(cls, name: str) -> logging.Logger:
        """Setup logger with file handler only (no console - causes QGIS issues)."""
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)

        # Prevent duplicate handlers
        if logger.handlers:
            return logger

        # Create formatter
        detailed_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )

        # File handler only - console logging causes issues in QGIS
        try:
            from qgis.PyQt.QtCore import QSettings
            settings = QSettings()
            profile_path = settings.value('userProfilePath', '')
            if profile_path:
                log_dir = os.path.join(profile_path, 'logs')
            else:
                log_dir = os.path.expanduser('~/.qgis3/logs')

            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, 'geodb_plugin.log')

            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(detailed_formatter)
            logger.addHandler(file_handler)
        except Exception:
            # If file logging fails, use a null handler to prevent errors
            logger.addHandler(logging.NullHandler())

        return logger

    @classmethod
    def register_ui_handler(cls, callback: Callable[[str, str], None]):
        """
        Register a UI callback to receive log messages.

        This should be called by the main dialog when it's initialized,
        passing its _log_message method.

        Args:
            callback: Function that takes (message, level) to display in UI
        """
        logger = cls.get_logger()

        # Remove existing UI handler if any
        cls.unregister_ui_handler()

        # Add new UI handler
        cls._ui_handler = UILogHandler(callback)
        cls._ui_handler.setLevel(logging.INFO)  # Don't show DEBUG in UI by default
        logger.addHandler(cls._ui_handler)

    @classmethod
    def unregister_ui_handler(cls):
        """Remove the UI handler (call when dialog closes)."""
        if cls._ui_handler and cls._instance:
            cls._instance.removeHandler(cls._ui_handler)
            cls._ui_handler = None


def log_function_call(func):
    """Decorator to log function calls."""
    def wrapper(*args, **kwargs):
        logger = PluginLogger.get_logger()
        logger.debug(f"Calling {func.__name__} with args={args}, kwargs={kwargs}")
        try:
            result = func(*args, **kwargs)
            logger.debug(f"{func.__name__} completed successfully")
            return result
        except Exception as e:
            logger.error(f"{func.__name__} failed with error: {e}", exc_info=True)
            raise
    return wrapper
