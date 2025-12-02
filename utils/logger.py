# -*- coding: utf-8 -*-
"""
Logging utilities for GeodbIO plugin.
"""
import logging
import os
from typing import Optional


class PluginLogger:
    """Custom logger for the plugin - file-only logging for reliability."""

    _instance: Optional[logging.Logger] = None

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