from PyQt5 import QtGui
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *


class SetTime(QDialog):
    def __init__(self, parent=None):
        super(SetTime, self).__init__(parent)
        self.setWindowTitle("设置抢单时间")
        icon = QtGui.QIcon()
        icon.addPixmap(QtGui.QPixmap("icons/later.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.setWindowIcon(icon)
        self.resize(300, 90)
        screen = QDesktopWidget().screenGeometry()
        self.move(screen.width() / 2.5, screen.height() / 2.5)

        # 在布局中添加部件
        layout = QVBoxLayout(self)
        self.datetime = QDateTimeEdit(self)
        self.datetime.setCalendarPopup(True)
        self.datetime.setDateTime(QDateTime.currentDateTime())
        layout.addWidget(self.datetime)

        # 使用两个button(ok和cancel)分别连接accept()和reject()槽函数
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # 从对话框中获取当前日期和时间
    def dateTime(self):
        return self.datetime.dateTime()

