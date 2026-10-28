"""Functional public room browser inside the suite; actions share the existing authenticated LAN client."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLineEdit,
                               QSpinBox, QTableWidget, QTableWidgetItem)

from akeno.i18n import t
from akeno.network.browser import RoomBrowser
from akeno.network.games import catalog
from akeno.ui.widgets import SubCard, button, confirm, label


class PublicRooms(SubCard):
    def __init__(self, page):
        super().__init__()
        self.page, self.rows, self.offset, self.next_offset = page, [], 0, None
        self.lay.addWidget(label(t('Public gaming rooms'), 'h2'))
        self.lay.addWidget(label(t('Rooms use encrypted relay-only connections. Players receive virtual LAN '
                                  'addresses. AKENO processes connection addresses; games may contact their own '
                                  'external services.'), 'dim', wrap=True))
        self.search = QLineEdit()
        self.search.setPlaceholderText(t('Search rooms'))
        self.search.setMaxLength(64)
        self.game = QComboBox()
        for item in catalog():
            self.game.addItem(item['name'], item['id'])
        self.region = QComboBox()
        for code, name in (('eu','Europe'),('na','North America'),('sa','South America'),('as','Asia'),
                           ('oc','Oceania'),('af','Africa'),('me','Middle East')):
            self.region.addItem(t(name), code)
        self.players = QSpinBox()
        self.players.setRange(0,16)
        self.players.setPrefix(t('Min. players: '))
        filters = QHBoxLayout()
        for w in (self.search,self.game,self.region,self.players):
            filters.addWidget(w)
        filters.addWidget(button(t('Refresh rooms'), 'refresh', on_click=lambda:self.refresh(0)))
        self.lay.addLayout(filters)
        self.table = QTableWidget(0,6)
        self.table.setHorizontalHeaderLabels([t('Room'),t('Game'),t('Region'),t('Players'),t('Status'),t('Password')])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMaximumHeight(300)
        self.lay.addWidget(self.table)
        self.summary = label(t('Refresh to find public rooms. A room is a LAN lobby, not a dedicated game server.'),
                             'muted',wrap=True)
        self.lay.addWidget(self.summary)
        actions = QHBoxLayout()
        for text, icon, action in ((t('Previous'),'chevron-left',self.previous),(t('Next'),'chevron-right',self.next),
                                  (t('Join selected room'),'link',self.join),(t('Report room'),'flag',self.report)):
            actions.addWidget(button(text,icon,on_click=action))
        self.lay.addLayout(actions)
        owner = QHBoxLayout()
        for text, action in ((t('Create public room'),self.create),(t('Publish current room'),self.publish),
                             (t('Make room private'),self.make_private),(t('Lock room'),self.lock)):
            owner.addWidget(button(text,'wifi',on_click=action))
        self.lay.addLayout(owner)

    def client(self):
        return RoomBrowser(self.page.lan())

    def refresh(self, offset=0):
        if not self.page.modules.enabled('lan_play'):
            return
        def show(result):
            self.rows = result['rooms']
            self.offset, self.next_offset = result['offset'], result['next_offset']
            self.table.setRowCount(len(self.rows))
            for row, room in enumerate(self.rows):
                values = [room['name'],room['game_id'],room['region'],f"{room['players']}/{room['capacity']}",
                          t(room['status']),t('Yes') if room['password_required'] else t('No')]
                for col,value in enumerate(values):
                    cell=QTableWidgetItem(value)
                    # Server-owned names must always remain plain text.
                    cell.setData(Qt.ItemDataRole.UserRole,room['room_id'])
                    self.table.setItem(row,col,cell)
            self.summary.setText(t('{n} public rooms. No player public IP addresses are listed.').format(n=len(self.rows)))
        self.page.work(lambda:self.client().browse(search=self.search.text().strip(),game_id=self.game.currentData(),
                      region=self.region.currentData(),minimum_players=self.players.value(),offset=offset),show)

    def previous(self):
        self.refresh(max(0,self.offset-20))

    def next(self):
        if self.next_offset is not None:
            self.refresh(self.next_offset)

    def selected(self):
        row=self.table.currentRow()
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def join(self):
        room=self.selected()
        if room is None:
            return
        if self.page.current_room():
            self.page.say(t('Leave the current room before joining another.'),'warn')
            return
        password=''
        if room['password_required']:
            password,ok=QInputDialog.getText(self,t('Join room'),t('Room password'),QLineEdit.EchoMode.Password)
            if not ok:
                return
        self.page.work(lambda:self.client().join(room['room_id'],password),self.page.room_changed)

    def create(self):
        if self.page.current_room():
            self.page.say(t('Leave the current room before joining another.'),'warn')
            return
        if not confirm(self,t('Create public room'),t('Publish this room name, game and region for other licensed '
                                                     'AKENO players? Connections stay relay-only.')):
            return
        self.page.work(lambda:self.client().create(self.page.room_name.text().strip(),self.game.currentData(),
                       self.region.currentData(),self.page.create_password.text()),self.page.room_changed)

    def change(self,visibility='public',locked=False):
        room=self.page.current_room()
        if not room:
            return
        self.page.work(lambda:self.client().publish(room,game_id=self.game.currentData(),region=self.region.currentData(),
                       visibility=visibility,locked=locked),lambda _:self.page.members())

    def publish(self):
        if confirm(self,t('Publish current room'),t('Make this room name, game and region visible to other AKENO players?')):
            self.change()

    def make_private(self):
        self.change('private')

    def lock(self):
        self.change(locked=True)

    def report(self):
        room=self.selected()
        if room is None:
            return
        categories=['spam','harassment','unsafe_content','other']
        category,ok=QInputDialog.getItem(self,t('Report room'),t('Reason'),categories,0,False)
        if ok:
            self.page.work(lambda:self.client().report(room['room_id'],category),
                           lambda _:self.page.say(t('Room report received.'),'success'))
