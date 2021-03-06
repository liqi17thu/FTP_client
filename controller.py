import os
from datetime import datetime
import threading

from PyQt5 import QtCore
from PyQt5.QtCore import QDir, pyqtSignal
from PyQt5.QtWidgets import *

from config import *


class TransferProcess(object):
    def __init__(self, local_file='', remote_file='', download=True, total_size=0, trans_size=0,
                 start_time=None, end_time=None, status=TransferStatus.Running):
        self.local_file = local_file
        self.remote_file = remote_file
        self.download = download
        self.total_size = total_size
        self.trans_size = trans_size
        self.start_time = start_time
        self.end_time = end_time
        self.status = status


class ClientCtrl(QtCore.QObject):
    # signals
    insert_response_signal = pyqtSignal(str)
    refresh_finished_signal = pyqtSignal()
    refresh_transferring_signal = pyqtSignal()
    update_single_transfer = pyqtSignal(TransferProcess)

    def __init__(self, model, view):
        super(ClientCtrl, self).__init__(view)
        self.model = model
        self.view = view

        self.mode = ClientMode.PORT

        self.local_cur_path = QDir.rootPath()
        self.remote_cur_path = '/'
        self.remote_file_size = {}

        # process pool
        self.running_proc = {}
        self.finished_proc = []

        # local path system
        # self.view.localSite.setText(self.local_cur_path)
        self.view.localSite.setText("/Users/liqi17thu/Desktop")
        self.model.localFileModel.setRootPath(self.local_cur_path)
        self.view.localFileView.setModel(self.model.localFileModel)
        self.view.localFileView.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 4):
            self.view.localFileView.header().setSectionResizeMode(col, QHeaderView.ResizeToContents)

        # signal slots
        self.view.PORT.clicked.connect(self.setPort)
        self.view.PASV.clicked.connect(self.setPasv)
        self.view.connect.clicked.connect(self.login)
        self.view.exit.clicked.connect(self.exit)

        self.view.localFileView.selectionModel().selectionChanged.connect(self.sync_local_path)
        self.view.localSiteBtn.clicked.connect(self.change_local_site)
        self.view.localCreateDir.clicked.connect(self.create_local_dir)
        self.view.localRename.clicked.connect(self.local_rename)
        self.view.localDelete.clicked.connect(self.local_delete)

        self.view.remoteFileWidget.selectionModel().selectionChanged.connect(self.sync_remote_path)
        self.view.remoteRename.clicked.connect(self.remote_rename)
        self.view.remoteDelete.clicked.connect(self.remote_delete)
        self.view.remoteSiteBtn.clicked.connect(self.change_remote_site)
        self.view.remoteCreateDir.clicked.connect(self.create_remote_dir)

        self.view.upload.clicked.connect(self.upload)
        self.view.download.clicked.connect(self.download)

        self.refresh_transferring_signal.connect(self.refresh_transferring_processing)
        self.refresh_finished_signal.connect(self.refresh_finished_processing)
        self.update_single_transfer.connect(self.update_single_transfer_process)
        self.insert_response_signal.connect(self.view.responses.insertPlainText)

    def setPort(self):
        self.mode = ClientMode.PORT

    def setPasv(self):
        self.mode = ClientMode.PASV

    def login(self):
        host = self.view.host.text()
        port = self.view.port.text()
        username = self.view.username.text()
        password = self.view.password.text()

        if not host:
            self.push_response(SYSTEM_HEADER + "5 please enter host to connect!")
            return

        if not port:
            self.push_response(SYSTEM_HEADER + "5 please enter port to connect!")
            return

        response = self.model.connect(host, port)
        self.push_response(response)

        if self.get_status_code(response)[0] == '5':
            return

        if not username:
            self.push_response(SYSTEM_HEADER + "5 please enter username to login!")
            return

        response = self.model.user(username)
        self.push_response(response)

        if self.get_status_code(response)[0] == '5':
            return

        if not password:
            self.push_response(SYSTEM_HEADER + "5 please enter password to login!")
            return

        response = self.model.password(password)
        self.push_response(response)

        if self.get_status_code(response)[0] == '5':
            return

        response, path = self.model.pwd()
        self.push_response(response)
        self.remote_cur_path = path
        self.view.remoteSite.setText(self.remote_cur_path)
        self.refresh_remote_site()
        self.refresh_transferring_processing()
        self.refresh_finished_processing()

    def exit(self):
        for proc_name in self.running_proc:
            self.running_proc[proc_name].status = TransferStatus.Paused

        self.model.quit()
        self.refresh_remote_site()
        self.refresh_transferring_processing()
        self.refresh_finished_processing()

    def thread_download(self, local_file, remote_file, size, resume=False):
        with threading.Lock():
            proc_hash = self.make_proc_hash(local_file, remote_file, size, download=True)
            offset = 0
            if proc_hash in self.running_proc:
                if self.running_proc[proc_hash].status == TransferStatus.Running:
                    self.push_response("system: 5 a transfer has been built for this transfer, please pause it first.")
                    return

                if resume:
                    if os.path.isfile(local_file):
                        offset = os.path.getsize(local_file)
                        self.running_proc[proc_hash].trans_size = offset
                    self.running_proc[proc_hash].status = TransferStatus.Running
            else:
                self.running_proc[proc_hash] = TransferProcess(local_file, remote_file, download=True, total_size=size,
                                                               start_time=datetime.now())

            self.refresh_transferring_signal.emit()

            self.push_response(self.model.type('I'))
            if self.mode == ClientMode.PORT:
                self.push_response(self.model.port())
            else:
                self.push_response(self.model.pasv())

            if offset > 0:
                fp = open(local_file, 'r+b')
                fp.seek(offset)
                self.push_response(self.model.rest(offset))
            else:
                fp = open(local_file, 'wb')

            def do_download(buf):
                if self.running_proc[proc_hash].status != TransferStatus.Running:
                    return False

                fp.write(buf)
                self.running_proc[proc_hash].trans_size += len(buf)
                self.update_single_transfer.emit(self.running_proc[proc_hash])
                return True

            self.push_response(self.model.retr(remote_file, do_download))
            self.finish_process(proc_hash)

    def download_file(self, local_file, remote_file, size, resume=False):
        if self.model.status == ClientStatus.DISCONNECT:
            self.push_response("system: 5 you haven't connected to a server yet.")
            return

        t = threading.Thread(target=self.thread_download, args=(local_file, remote_file, size, resume,))
        t.start()

    def download(self):
        if self.model.status == ClientStatus.DISCONNECT:
            self.push_response("system: 5 you haven't connected to a server yet.")
            return

        item = self.view.remoteFileWidget.currentItem()
        if item is None:
            self.push_response("system: 5 no file selected.")
            return

        filename = item.text(FileHeader.Name.value)

        local_file = os.path.join(self.local_cur_path, filename)
        remote_file = os.path.join(self.remote_cur_path, filename)
        size = self.remote_file_size[filename] if filename in self.remote_file_size else 0

        self.download_file(local_file, remote_file, size)

    def thread_upload(self, local_file, remote_file, size, resume=False):
        with threading.Lock():
            proc_hash = self.make_proc_hash(local_file, remote_file, size, download=False)
            offset = 0
            if proc_hash in self.running_proc:
                if self.running_proc[proc_hash].status == TransferStatus.Running:
                    self.push_response("system: 5 a transfer has been built for this transfer, please pause it first.")
                    return

                if resume:
                    response, offset = self.model.size(self.running_proc[proc_hash].remote_file)
                    self.push_response(response)
                    self.running_proc[proc_hash].status = TransferStatus.Running
                    self.running_proc[proc_hash].trans_size = offset
            else:
                self.running_proc[proc_hash] = TransferProcess(local_file, remote_file, download=False, total_size=size,
                                                               start_time=datetime.now())

            self.refresh_transferring_signal.emit()

            self.push_response(self.model.type('I'))
            if self.mode == ClientMode.PORT:
                self.push_response(self.model.port())
            else:
                self.push_response(self.model.pasv())

            fp = open(local_file, 'rb')

            if offset > 0:
                fp.seek(offset)

            def do_upload(n):
                if self.running_proc[proc_hash].status != TransferStatus.Running:
                    return ''

                buf = fp.read(n)
                self.running_proc[proc_hash].trans_size += len(buf)
                self.update_single_transfer.emit(self.running_proc[proc_hash])
                return buf

            if offset > 0:
                self.push_response(self.model.appe(remote_file, do_upload))
            else:
                self.push_response(self.model.stor(remote_file, do_upload))
            self.finish_process(proc_hash)

            # update view
            self.refresh_remote_site()

    def upload_file(self, local_file, remote_file, size, resume=False):
        if self.model.status == ClientStatus.DISCONNECT:
            self.push_response("system: 5 you haven't connected to a server yet.")
            return

        t = threading.Thread(target=self.thread_upload, args=(local_file, remote_file, size, resume,))
        t.start()

    def upload(self):
        if self.model.status == ClientStatus.DISCONNECT:
            self.push_response("system: 5 you haven't connected to a server yet.")
            return

        local_file = self.model.localFileModel.filePath(self.view.localFileView.selectedIndexes()[0])
        remote_file = os.path.join(self.remote_cur_path, local_file.split('/')[-1])
        size = os.path.getsize(local_file)

        self.upload_file(local_file, remote_file, size)

    def pause_transfer(self, running_proc):
        running_proc.status = TransferStatus.Paused

    def resume_transfer(self, running_proc):
        if running_proc.download:
            self.download_file(running_proc.local_file, running_proc.remote_file, running_proc.total_size, resume=True)
        else:
            self.upload_file(running_proc.local_file, running_proc.remote_file, running_proc.total_size, resume=True)

    def pause_or_resume_transfer(self, running_proc):
        if running_proc.status == TransferStatus.Paused:
            self.resume_transfer(running_proc)
        elif running_proc.status == TransferStatus.Running:
            self.pause_transfer(running_proc)
        else:
            raise RuntimeError

    def cancel_transfer(self, running_proc):
        self.cancel_process(
            self.make_proc_hash(running_proc.local_file, running_proc.remote_file, running_proc.total_size,
                                running_proc.download))

    def change_local_site(self):
        new_path = self.view.localSite.text()
        if not os.path.isdir(new_path):
            self.push_response("system: 5 invalid path.")
            return

        self.local_cur_path = new_path
        self.view.localFileView.setRootIndex(self.model.localFileModel.setRootPath(self.local_cur_path))

    def sync_local_path(self):
        selected_path = self.model.localFileModel.filePath(self.view.localFileView.selectedIndexes()[0])
        self.view.localSite.setText(selected_path)

    def sync_remote_path(self):
        items = self.view.remoteFileWidget.selectedItems()
        if len(items) > 0:
            item = items[0]
            filename = item.text(FileHeader.Name.value)
            selected_path = os.path.join(self.remote_cur_path, filename)
        else:
            selected_path = self.remote_cur_path
        self.view.remoteSite.setText(selected_path)

    def refresh_remote_site(self):
        if self.model.status == ClientStatus.DISCONNECT:
            self.view.refresh_remote_widget([])
            return

        try:
            if self.mode == ClientMode.PORT:
                self.push_response(self.model.port())
            else:
                self.push_response(self.model.pasv())
            response, file_list = self.model.list()
            self.push_response(response)

            self.remote_file_size = {}
            files = self.parse_file_list(file_list)
            self.view.refresh_remote_widget(files)
        except:
            self.push_response("server: 5 fail to get remote list.")
            self.view.refresh_remote_widget([])

    def update_single_transfer_process(self, proc):
        self.view.update_transfer_item(proc)

    def refresh_finished_processing(self):
        self.view.refresh_finished_widget(self.finished_proc)

    def refresh_transferring_processing(self):
        self.view.refresh_transfer_widget(self.running_proc, self.pause_or_resume_transfer, self.cancel_transfer)

    def change_remote_site(self):
        if self.model.status == ClientStatus.DISCONNECT:
            self.push_response("system: 5 you haven't connected to a server yet.")
            return

        self.remote_cur_path = self.view.remoteSite.text()

        response = self.model.cwd(self.remote_cur_path)
        self.push_response(response)
        if self.get_status_code(response)[0] == '5':
            return
        self.refresh_remote_site()

    def create_local_dir(self):
        class MyDialog(QDialog):
            def __init__(self):
                super(MyDialog, self).__init__()

                self.setWindowTitle("Please Enter Directory Name")
                self.setFixedWidth(400)

                QBtn = QDialogButtonBox.Ok | QDialogButtonBox.Cancel

                self.lineEdit = QLineEdit()

                self.buttonBox = QDialogButtonBox(QBtn)
                self.buttonBox.accepted.connect(self.accept)
                self.buttonBox.rejected.connect(self.reject)

                self.layout = QVBoxLayout()
                self.layout.addWidget(self.lineEdit)
                self.layout.addWidget(self.buttonBox)
                self.setLayout(self.layout)

        dlg = MyDialog()
        if not dlg.exec_():
            return

        selections = self.view.localFileView.selectedIndexes()
        if len(selections) <= 0:
            self.push_response("system: 5 no file selected.")
            return

        pathname = self.model.localFileModel.filePath(selections[0])

        new_dir_name = dlg.lineEdit.text()
        if os.path.isdir(pathname):
            new_dir_path = os.path.join(pathname, new_dir_name)
        else:
            new_dir_path = os.path.join(os.path.dirname(pathname), new_dir_name)

        if not os.path.exists(new_dir_path):
            os.makedirs(new_dir_path)
        else:
            self.push_response(f"system: 5 {new_dir_path} already exists.")

    def local_rename(self):
        class MyDialog(QDialog):
            def __init__(self, old_name):
                super(MyDialog, self).__init__()

                self.setWindowTitle("Please Enter New Name")
                self.setFixedWidth(400)

                QBtn = QDialogButtonBox.Ok | QDialogButtonBox.Cancel

                self.lineEdit = QLineEdit()
                self.lineEdit.setText(old_name)

                self.buttonBox = QDialogButtonBox(QBtn)
                self.buttonBox.accepted.connect(self.accept)
                self.buttonBox.rejected.connect(self.reject)

                self.layout = QVBoxLayout()
                self.layout.addWidget(self.lineEdit)
                self.layout.addWidget(self.buttonBox)
                self.setLayout(self.layout)

        selections = self.view.localFileView.selectedIndexes()
        if len(selections) <= 0:
            self.push_response("system: 5 no file selected.")
            return

        filepath = self.model.localFileModel.filePath(selections[0])
        root_path = '/'.join(filepath.split('/')[:-1])
        old_name = filepath.split('/')[-1]
        dlg = MyDialog(old_name)
        if not dlg.exec_():
            return

        new_name = dlg.lineEdit.text()
        os.rename(os.path.join(root_path, old_name), os.path.join(root_path, new_name))

    def local_delete(self):
        if self.model.status == ClientStatus.DISCONNECT:
            self.push_response("system: 5 you haven't connected to a server yet.")
            return

        local_path = self.model.localFileModel.filePath(self.view.localFileView.selectedIndexes()[0])
        try:
            if os.path.isdir(local_path):
                os.rmdir(local_path)
            else:
                os.remove(local_path)
        except Exception as e:
            self.push_response(f'system: 5 {e}')

    def create_remote_dir(self):
        if self.model.status == ClientStatus.DISCONNECT:
            self.push_response("system: 5 you haven't connected to a server yet.")
            return

        class MyDialog(QDialog):
            def __init__(self):
                super(MyDialog, self).__init__()

                self.setWindowTitle("Please Enter Directory Name")
                self.setFixedWidth(400)

                QBtn = QDialogButtonBox.Ok | QDialogButtonBox.Cancel

                self.lineEdit = QLineEdit()

                self.buttonBox = QDialogButtonBox(QBtn)
                self.buttonBox.accepted.connect(self.accept)
                self.buttonBox.rejected.connect(self.reject)

                self.layout = QVBoxLayout()
                self.layout.addWidget(self.lineEdit)
                self.layout.addWidget(self.buttonBox)
                self.setLayout(self.layout)

        dlg = MyDialog()
        if not dlg.exec_():
            return

        new_dir_name = dlg.lineEdit.text()
        response = self.model.mkd(new_dir_name)
        self.push_response(response)
        self.refresh_remote_site()

    def remote_delete(self):
        if self.model.status == ClientStatus.DISCONNECT:
            self.push_response("system: 5 you haven't connected to a server yet.")
            return

        item = self.view.remoteFileWidget.currentItem()
        name = item.text(FileHeader.Name.value)
        if item.text(FileHeader.Type.value) == FileType.Folder.value:
            response = self.model.rmd(name)
        else:
            response = self.model.dele(name)
        self.push_response(response)
        self.refresh_remote_site()

    def remote_rename(self):
        if self.model.status == ClientStatus.DISCONNECT:
            self.push_response("system: 5 you haven't connected to a server yet.")
            return

        class MyDialog(QDialog):
            def __init__(self, old_name):
                super(MyDialog, self).__init__()

                self.setWindowTitle("Please Enter New Name")
                self.setFixedWidth(400)

                QBtn = QDialogButtonBox.Ok | QDialogButtonBox.Cancel

                self.lineEdit = QLineEdit()
                self.lineEdit.setText(old_name)

                self.buttonBox = QDialogButtonBox(QBtn)
                self.buttonBox.accepted.connect(self.accept)
                self.buttonBox.rejected.connect(self.reject)

                self.layout = QVBoxLayout()
                self.layout.addWidget(self.lineEdit)
                self.layout.addWidget(self.buttonBox)
                self.setLayout(self.layout)

        item = self.view.remoteFileWidget.currentItem()
        old_name = item.text(FileHeader.Name.value)

        dlg = MyDialog(old_name)
        if not dlg.exec_():
            return

        new_name = dlg.lineEdit.text()

        self.push_response(self.model.rnfr(old_name))
        self.push_response(self.model.rnto(new_name))
        self.refresh_remote_site()

    # help functions
    @staticmethod
    def get_status_code(msg):
        return msg.split(' ')[1]

    def push_response(self, response):
        if not response.endswith('\n'):
            response += '\n'

        self.insert_response_signal.emit(response)

    def parse_single_file_list(self, list):
        lists = list.split()
        mode = lists[0]
        # link = lists[1]
        owner = lists[2]
        # group = lists[3]
        size = lists[4]
        last_modified = ' '.join(lists[5:8])
        filename = lists[8]
        file_type = FileType.Folder.value if mode[0] == 'd' else FileType.File.value

        # load file size
        self.remote_file_size[filename] = int(size)

        return filename, size, file_type, last_modified, mode, owner

    def parse_file_list(self, file_list):
        lists = []
        for list in file_list.splitlines(keepends=False)[1:]:
            lists.append(self.parse_single_file_list(list))
        return lists

    def make_proc_hash(self, local_file, remote_file, size, download):
        if download:
            return local_file + '<-' + remote_file + "_" + str(size)
        else:
            return local_file + '->' + remote_file + "_" + str(size)

    def cancel_process(self, proc_hash):
        self.push_response(f"system: 5 cancel job {proc_hash}")
        self.running_proc[proc_hash].status = TransferStatus.Canceled
        self.running_proc[proc_hash].end_time = datetime.now()

        # erase the unfinished file
        if self.running_proc[proc_hash].download:
            if os.path.exists(self.running_proc[proc_hash].local_file):
                os.remove(self.running_proc[proc_hash].local_file)
        else:
            self.push_response(self.model.dele(self.running_proc[proc_hash].remote_file))
        self.finished_proc.append(self.running_proc[proc_hash])
        del self.running_proc[proc_hash]

        self.refresh_transferring_signal.emit()
        self.refresh_finished_signal.emit()

    def finish_process(self, proc_hash):
        if self.running_proc[proc_hash].status == TransferStatus.Paused:
            return

        if self.running_proc[proc_hash].trans_size != self.running_proc[proc_hash].total_size:
            self.push_response("system: 5 unmatched size, something might be wrong.")
            self.running_proc[proc_hash].status = TransferStatus.Failed
        else:
            self.running_proc[proc_hash].status = TransferStatus.Finished
        self.running_proc[proc_hash].end_time = datetime.now()
        self.finished_proc.append(self.running_proc[proc_hash])
        del self.running_proc[proc_hash]

        self.refresh_transferring_signal.emit()
        self.refresh_finished_signal.emit()


class Test(object):
    def __init__(self):
        self.x = 10

    def foo(self):
        y = 20

        def bar():
            print(self.x)
            print(y)

        bar()


if __name__ == '__main__':
    test = Test()
    test.foo()
