import logging

# --- Logging Setup ---


def setup_logging():

    logger = logging.getLogger("MarylandScraper")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    info_handler = logging.FileHandler("info.log", mode="a", encoding="utf-8")
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(formatter)

    error_handler = logging.FileHandler("error.log", mode="a", encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(info_handler)
        logger.addHandler(error_handler)

    return logger
