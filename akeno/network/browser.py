"""Desktop interface for the authenticated LAN browser, sharing the existing LanClient account and DPAPI identity."""
from urllib.parse import urlencode

from akeno.network.games import profile


class RoomBrowser:
    def __init__(self, lan_client):
        self.lan = lan_client

    def browse(self, *, search='', game_id='', region='', minimum_players=0, open_only=True, limit=20, offset=0):
        i = self.lan._identity()
        query = urlencode({'device_id':i['device_id'],'search':search,'game_id':game_id,'region':region,
                           'minimum_players':minimum_players,'open_only':str(open_only).lower(),
                           'limit':limit,'offset':offset})
        return self.lan._call('GET','browser/rooms?'+query)

    def create(self, name, game_id, region, password='', capacity=16):
        profile(game_id)
        i = self.lan._identity()
        result = self.lan._call('POST','browser/rooms',{'device_id':i['device_id'],'public_key':i['public_key'],
                                'name':name,'game_id':game_id,'region':region,'password':password,'capacity':capacity})
        self.lan.identity.save({**i,'room_id':result['room_id']})
        return result

    def publish(self, room, *, game_id, region, visibility='public', capacity=16, locked=False):
        profile(game_id)
        i = self.lan._identity()
        return self.lan._call('PATCH','browser/rooms/'+self.lan._room(room),{'device_id':i['device_id'],
                             'game_id':game_id,'region':region,'visibility':visibility,'capacity':capacity,'locked':locked})

    def join(self, room, password=''):
        i = self.lan._identity()
        result = self.lan._call('POST','browser/rooms/'+self.lan._room(room)+'/join',
                               {'device_id':i['device_id'],'public_key':i['public_key'],'password':password})
        self.lan.identity.save({**i,'room_id':result['room_id']})
        return result

    def report(self, room, category):
        i = self.lan._identity()
        return self.lan._call('POST','browser/rooms/'+self.lan._room(room)+'/reports',
                             {'device_id':i['device_id'],'category':category})
