
from PySide6.QtWidgets import QApplication
import sys
from pathlib import Path

src_path = Path(__file__).parent / "src"
sys.path.append(str(src_path))
from gui import MainWindow

print(sys.path)

def main() -> None:
    app = QApplication([])
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()