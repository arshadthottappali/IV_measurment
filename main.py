import tkinter as tk
import logging

from gui import KeithleyUI


def setup_logging():
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    file_handler = logging.FileHandler("keithley_backend.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def main():
    setup_logging()
    root = tk.Tk()
    KeithleyUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
