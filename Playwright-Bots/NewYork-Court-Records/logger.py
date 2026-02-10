import logging

# --- Logging Setup ---


def setup_logging():
    # Create a custom logger
    logger = logging.getLogger("MarylandScraper")
    logger.setLevel(logging.INFO)

    # Create formatters and add them to the handlers
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    # Create file handlers for info and error logs
    info_handler = logging.FileHandler("info.log", mode="a", encoding="utf-8")
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(formatter)

    error_handler = logging.FileHandler("error.log", mode="a", encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    # Add the handlers to the logger
    if not logger.handlers:
        logger.addHandler(info_handler)
        logger.addHandler(error_handler)

    return logger
