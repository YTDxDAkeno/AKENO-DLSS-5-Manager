"""Functional public room browser inside the suite; actions share the existing authenticated LAN client."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLineEdit,
                               QSpinBox, QTableWidget, QTableWidgetItem)

from akeno.i18n import msg, t
from akeno.network.browser import RoomBrowser
from akeno.network.games import catalog
from akeno.ui.widgets import SubCard, button, confirm, label

REGIONS = (('eu', msg('Europe')), ('na', msg('North America')), ('sa', msg('South America')), ('as', msg('Asia')),
           ('oc', msg('Oceania')), ('af', msg('Africa')), ('me', msg('Middle East')))
STATUS = {'open': msg('open'), 'locked': msg('locked'), 'full': msg('full')}
REPORTS = (('spam', msg('Spam')), ('harassment', msg('Harassment')), ('unsafe_content', msg('Unsafe content')),
           ('other', msg('Other')))


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
        # Empty filter values mean "all" for the browser API; publishing a room needs a concrete game and region.
        self.game = QComboBox()
        self.game.addItem(t('All games'), '')
        self.names = {}
        for item in catalog():
            self.game.addItem(item['name'], item['id'])
            self.names[item['id']] = item['name']
        self.region = QComboBox()
        self.region.addItem(t('All regions'), '')
        for code, name in REGIONS:
            self.region.addItem(t(name), code)
        self.players = QSpinBox()
        self.players.setRange(0,16)
        self.players.setPrefix(t('Min. players: '))
        self.players.setMinimumWidth(self.players.fontMetrics().horizontalAdvance(t('Min. players: ') + '16') + 48)
        filters = QHBoxLayout()
        filters.addWidget(self.search,2)
        for w in (self.game,self.region,self.players):
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
        for text, icon, action in ((t('Create public room'),'plus',self.create),
                                   (t('Publish current room'),'globe',self.publish),
                                   (t('Make room private'),'eye-off',self.make_private),
                                   (t('Lock room'),'lock',self.lock)):
            owner.addWidget(button(text,icon,on_click=action))
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
                region = dict(REGIONS).get(room['region'])
                values = [room['name'],self.names.get(room['game_id'],room['game_id']),t(region) if region else
                          room['region'],f"{room['players']}/{room['capacity']}",
                          t(STATUS.get(room['status'],room['status'])),t('Yes') if room['password_required'] else t('No')]
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

    def listing(self):
        """Game and region of a public listing: the filters must name one of each (not "All")."""
        if not self.game.currentData() or not self.region.currentData():
            self.page.say(t('Choose a game and a region for the public room first.'),'warn')
            return None
        return self.game.currentData(), self.region.currentData()

    def create(self):
        if self.page.current_room():
            self.page.say(t('Leave the current room before joining another.'),'warn')
            return
        if self.listing() is None:
            return
        if not confirm(self,t('Create public room'),t('Publish this room name, game and region for other licensed '
                                                     'AKENO players? Connections stay relay-only.')):
            return
        self.page.work(lambda:self.client().create(self.page.room_name.text().strip(),self.game.currentData(),
                       self.region.currentData(),self.page.create_password.text()),self.page.room_changed)

    def change(self,visibility='public',locked=False):
        room=self.page.current_room()
        if not room or self.listing() is None:
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
        names=[t(name) for _,name in REPORTS]
        choice,ok=QInputDialog.getItem(self,t('Report room'),t('Reason'),names,0,False)
        if ok:
            category=REPORTS[names.index(choice)][0]
            self.page.work(lambda:self.client().report(room['room_id'],category),
                           lambda _:self.page.say(t('Room report received.'),'success'))
