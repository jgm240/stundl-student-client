"""Entry point of the packaged Stundl Student Client.app (PyInstaller)."""
import multiprocessing

import ssc

if __name__ == "__main__":
    multiprocessing.freeze_support()
    ssc.main()
