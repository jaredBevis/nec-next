"""Entry point: python -m necnext [deck.nec]"""
import faulthandler
import os
import sys
import traceback

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "necnext_error.log")


def _log_file():
    try:
        return open(LOG, "w", encoding="utf-8")
    except OSError:
        import tempfile
        return open(os.path.join(tempfile.gettempdir(), "necnext_error.log"), "w", encoding="utf-8")


def main():
    log = _log_file()
    # Native crashes (segfaults in Qt/OpenGL drivers) are written here too.
    faulthandler.enable(file=log, all_threads=True)
    log.write(f"Python {sys.version}\nPlatform {sys.platform}\nArgs {sys.argv}\n")
    log.flush()

    def excepthook(et, ev, tb):
        text = "".join(traceback.format_exception(et, ev, tb))
        log.write("\nUnhandled exception:\n" + text)
        log.flush()
        sys.__stderr__ and sys.__stderr__.write(text)
        try:
            from .ui.compat import QtWidgets
            if QtWidgets.QApplication.instance():
                QtWidgets.QMessageBox.critical(None, "NEC Next error",
                                               f"{ev}\n\nDetails were written to:\n{log.name}")
        except Exception:
            pass
    sys.excepthook = excepthook

    try:
        if "--no-gl" in sys.argv:
            os.environ["NECNEXT_NO_GL"] = "1"
            sys.argv.remove("--no-gl")
        from .ui.compat import QT_LIB, QtCore, QtWidgets, exec_
        log.write(f"Qt binding {QT_LIB} {QtCore.qVersion()}\n")
        log.flush()
        from .ui import theme
        from .ui.mainwindow import MainWindow
        if QtCore.qVersion().startswith("5."):
            QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
            QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
        app = QtWidgets.QApplication(sys.argv)
        app.setApplicationName("NEC Next")
        app.setOrganizationName("NecNext")
        theme.apply(app)
        log.write("Creating main window\n")
        log.flush()
        path = sys.argv[1] if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) else None
        w = MainWindow(path)
        w.show()
        log.write("Main window shown\n")
        log.flush()
        rc = exec_(app)
        log.write(f"Exited normally ({rc})\n")
        return rc
    except Exception:
        excepthook(*sys.exc_info())
        return 1


if __name__ == "__main__":
    sys.exit(main())
