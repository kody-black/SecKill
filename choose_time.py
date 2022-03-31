from PyQt5 import QtGui
from PyQt5.Qt import *


class DateTimeEditDemo(QDialog):
    def __init__(self):
        super().__init__()
        # 设置控件(大小、位置、样式...)
        self.setWindowTitle("设置抢单时间")
        icon = QtGui.QIcon()
        icon.addPixmap(QtGui.QPixmap("icons/later.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.setWindowIcon(icon)
        self.resize(300, 90)
        screen = QDesktopWidget().screenGeometry()
        self.move(screen.width()/2.5,screen.height()/2.5)
        self.setup_ui()

    def setup_ui(self):
        vlayout = QVBoxLayout()
        self.dateEdit = QDateTimeEdit(QDateTime.currentDateTime(), self)
        self.dateEdit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        # 设置最小日期
        self.dateEdit.setMinimumDate(QDate.currentDate().addDays(-365))
        # 设置最大日期
        self.dateEdit.setMaximumDate(QDate.currentDate().addDays(365))
        # True，弹出日历控件
        self.dateEdit.setCalendarPopup(True)
        # 建立信号与相应槽的连接
        self.dateEdit.dateChanged.connect(self.onDateChanged)
        self.dateEdit.dateTimeChanged.connect(self.onDateTimeChanged)
        self.dateEdit.timeChanged.connect(self.onTimeChanged)
        self.btn = QPushButton('获得日期和时间')
        self.btn.clicked.connect(self.onButtonClick)
        vlayout.addWidget(self.dateEdit)
        vlayout.addWidget(self.btn)
        self.setLayout(vlayout)

    # 日期发生改变时执行
    @staticmethod
    def onDateChanged(date):
        print(date)

    # 无论日期还是时间发生改变，都会执行
    @staticmethod
    def onDateTimeChanged(dateTime):
        print(dateTime)

    # 时间发生改变时执行
    @staticmethod
    def onTimeChanged(time):
        print(time)

    def onButtonClick(self):
        dateTime = self.dateEdit.dateTime()  # 最大日期
        maxDate = self.dateEdit.maximumDate()  # 最大日期时间
        maxDateTime = self.dateEdit.maximumDateTime()  # 最大时间
        maxTime = self.dateEdit.maximumTime()  # 最小日期
        minDate = self.dateEdit.minimumDate()  # 最小日期时间
        minDateTime = self.dateEdit.minimumDateTime()  # 最小时间
        minTime = self.dateEdit.minimumTime()
        print('\n选择日期时间')
        print('dateTime=%s' % str(dateTime))
        print('maxDate=%s' % str(maxDate))
        print('maxDateTime=%s' % str(maxDateTime))
        print('maxTime=%s' % str(maxTime))
        print('minDate=%s' % str(minDate))
        print('minDateTime=%s' % str(minDateTime))
        print('minTime=%s' % str(minTime))
