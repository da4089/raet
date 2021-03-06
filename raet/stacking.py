# -*- coding: utf-8 -*-
'''
stacking.py raet protocol stacking classes
'''
# pylint: skip-file
# pylint: disable=W0611

# Import python libs
import socket
import os
import errno

from collections import deque,  Mapping
try:
    import simplejson as json
except ImportError:
    import json

try:
    import msgpack
except ImportError:
    mspack = None

# Import ioflo libs
from ioflo.base.odicting import odict
from ioflo.base import aiding
from ioflo.base import storing

from . import raeting
from . import keeping
from . import lotting

from ioflo.base.consoling import getConsole
console = getConsole()

class Stack(object):
    '''
    RAET protocol base stack object.
    Should be subclassed for specific transport type such as UDP or UXD
    '''
    Count = 0

    def __init__(self,
                 name='',
                 version=raeting.VERSION,
                 store=None,
                 keep=None,
                 dirpath='',
                 basedirpath='',
                 local=None,
                 localname='',
                 bufcnt=2,
                 server=None,
                 rxMsgs=None,
                 txMsgs=None,
                 rxes=None,
                 txes=None,
                 stats=None,
                 clean=False,
                 ):
        '''
        Setup Stack instance
        '''
        if not name:
            name = "stack{0}".format(Stack.Count)
            Stack.Count += 1

        self.name = name
        self.version = version
        self.store = store or storing.Store(stamp=0.0)

        self.keep = keep or keeping.LotKeep(dirpath=dirpath,
                                            basedirpath=basedirpath,
                                            stackname=self.name)

        if clean: # clear persisted data so uses provided or default data
            self.clearLocal()
            self.clearRemoteKeeps()

        self.loadLocal(local=local, name=localname) # load local data from saved data else passed in local
        self.remotes = odict() # remotes indexed by uid
        self.uids = odict() # remote uids indexed by name
        self.loadRemotes() # load remotes from saved data

        for remote in self.remotes.values():
            remote.nextSid()

        self.bufcnt = bufcnt
        if not server:
            server = self.serverFromLocal()

        self.server = server
        if self.server:
            if not self.server.reopen():  # open socket
                raise raeting.StackError("Stack '{0}': Failed opening server at"
                            " '{1}'\n".format(self.name, self.server.ha))
            if self.local:
                self.local.ha = self.server.ha  # update local host address after open

            console.verbose("Stack '{0}': Opened server at '{1}'\n".format(self.name, self.local.ha))

        self.rxMsgs = rxMsgs if rxMsgs is not None else deque() # messages received
        self.txMsgs = txMsgs if txMsgs is not None else deque() # messages to transmit
        self.rxes = rxes if rxes is not None else deque() # udp packets received
        self.txes = txes if txes is not None else deque() # udp packet to transmit
        self.stats = stats if stats is not None else odict() # udp statistics
        self.statTimer = aiding.StoreTimer(self.store)

        self.dumpLocal() # save local data
        self.dumpRemotes() # save remote data

    def serverFromLocal(self):
        '''
        Create server from local data
        '''
        return None

    def addRemote(self, remote, uid=None):
        '''
        Add a remote  to .remotes
        '''
        if uid is None:
            uid = remote.uid
        if uid and (uid in self.remotes or uid == self.local.uid):
            emsg = "Cannot add remote at uid '{0}', alreadys exists".format(uid)
            raise raeting.StackError(emsg)
        remote.stack = self
        self.remotes[uid] = remote
        if remote.name in self.uids or remote.name == self.local.name:
            emsg = "Cannot add remote with name '{0}', alreadys exists".format(remote.name)
            raise raeting.StackError(emsg)
        self.uids[remote.name] = remote.uid

    def moveRemote(self, old, new):
        '''
        Move remote at key old uid with key new uid and replace the odict key index
        so order is the same
        '''
        if new in self.remotes or new == self.local.uid:
            emsg = "Cannot move, remote to '{0}', already exists".format(new)
            raise raeting.StackError(emsg)

        if old not in self.remotes:
            emsg = "Cannot move remote '{0}', does not exist".format(old)
            raise raeting.StackError(emsg)

        remote = self.remotes[old]
        self.clearRemote(remote)
        index = self.remotes.keys().index(old)
        remote.uid = new
        self.uids[remote.name] = new
        del self.remotes[old]
        self.remotes.insert(index, remote.uid, remote)

    def renameRemote(self, old, new):
        '''
        rename remote with old name to new name but keep same index
        '''
        if new in self.uids or new == self.local.name:
            emsg = "Cannot rename remote to '{0}', already exists".format(new)
            raise raeting.StackError(emsg)

        if old not in self.uids:
            emsg = "Cannot rename remote '{0}', does not exist".format(old)
            raise raeting.StackError(emsg)

        uid = self.uids[old]
        remote = self.remotes[uid]
        remote.name = new
        index = self.uids.keys().index(old)
        del self.uids[old]
        self.uids.insert(index, remote.name, remote.uid)

    def removeRemote(self, uid):
        '''
        Remove remote at key uid
        '''
        if uid not in self.remotes:
            emsg = "Cannot remove remote '{0}', does not exist".format(uid)
            raise raeting.StackError(emsg)

        remote = self.remotes[uid]
        self.clearRemote(remote)
        del self.remotes[uid]
        del self.uids[remote.name]

    def removeAllRemotes(self):
        '''
        Remove all the remotes
        '''
        uids = self.remotes.keys() #make copy since changing .remotes in-place
        for uid in uids:
            self.removeRemote(uid)

    def fetchRemoteByName(self, name):
        '''
        Search for remote with matching name
        Return remote if found Otherwise return None
        '''
        return self.remotes.get(self.uids.get(name))

    def dumpLocal(self):
        '''
        Dump keeps of local
        '''
        self.keep.dumpLocal(self.local)

    def loadLocal(self, local=None, name=''):
        '''
        Load self.local from keep file else local or new
        '''
        data = self.keep.loadLocalData()
        if data and self.keep.verifyLocalData(data):
            self.local = lotting.LocalLot(stack=self,
                                          uid=data['uid'],
                                          name=data['name'],
                                          ha=data['ha'],
                                          sid = data['sid'])
            self.name = self.local.name

        elif local:
            local.stack = self
            self.local = local

        else:
            self.local = lotting.LocalLot(stack=self, name=name)

    def clearLocal(self):
        '''
        Clear local keep
        '''
        self.keep.clearLocalData()

    def dumpRemote(self, remote):
        '''
        Dump keeps of remote
        '''
        self.keep.dumpRemote(remote)

    def dumpRemotes(self):
        '''
        Dump all remotes data to keep files
        '''
        self.clearRemotes()
        datadict = odict()
        for remote in self.remotes.values():
            self.dumpRemote(remote)

    def loadRemotes(self):
        '''
        Load and add remote for each remote file
        '''
        datadict = self.keep.loadAllRemoteData()
        for data in datadict.values():
            if self.keep.verifyRemoteData(data):
                lot = lotting.Lot(stack=self,
                                  uid=data['uid'],
                                  name=data['name'],
                                  ha=data['ha'],
                                  sid=data['sid'])
                self.addRemote(remote)

    def clearRemote(self, remote):
        '''
        Clear remote keep of remote
        '''
        self.keep.clearRemoteData(remote.uid)

    def clearRemotes(self):
        '''
        Clear remote keeps of .remotes
        '''
        for remote in self.remotes.values():
            self.clearRemote(remote)

    def clearRemoteKeeps(self):
        '''
        Clear all remote keeps
        '''
        self.keep.clearAllRemoteData()

    def incStat(self, key, delta=1):
        '''
        Increment stat key counter by delta
        '''
        if key in self.stats:
            self.stats[key] += delta
        else:
            self.stats[key] = delta

    def updateStat(self, key, value):
        '''
        Set stat key to value
        '''
        self.stats[key] = value

    def clearStat(self, key):
        '''
        Set the specified state counter to zero
        '''
        if key in self.stats:
            self.stats[key] = 0

    def clearStats(self):
        '''
        Set all the stat counters to zero and reset the timer
        '''
        for key, value in self.stats.items():
            self.stats[key] = 0
        self.statTimer.restart()

    def _handleOneReceived(self):
        '''
        Handle one received message from server
        assumes that there is a server
        '''
        rx, ra = self.server.receive()  # if no data the duple is ('',None)
        if not rx:  # no received data
            return False
        # triple = ( packet, source address, destination address)
        self.rxes.append((rx, ra, self.server.ha))
        return True

    def serviceReceives(self):
        '''
        Retrieve from server all recieved and put on the rxes deque
        '''
        if self.server:
            while self._handleOneReceived():
                pass

    def serviceReceiveOnce(self):
        '''
        Retrieve from server one recieved and put on the rxes deque
        '''
        if self.server:
            self._handleOneReceived()

    def _handleOneRx(self):
        '''
        Handle on message from .rxes deque
        Assumes that there is a message on the .rxes deque
        '''
        raw, sa, da = self.rxes.popleft()
        console.verbose("{0} received raw message\n{1}\n".format(self.name, raw))
        processRx(received=raw)

    def serviceRxes(self):
        '''
        Process all messages in .rxes deque
        '''
        while self.rxes:
            self._handleOneRx()

    def serviceRxOnce(self):
        '''
        Process one messages in .rxes deque
        '''
        if self.rxes:
            self.handleOnceRx()

    def processRx(self, received):
        '''
        Process
        '''
        pass

    def transmit(self, msg, duid=None):
        '''
        Append duple (msg, duid) to .txMsgs deque
        If msg is not mapping then raises exception
        If duid is None then it will default to the first entry in .remotes
        '''
        if not isinstance(msg, Mapping):
            emsg = "Invalid msg, not a mapping {0}\n".format(msg)
            console.terse(emsg)
            self.incStat("invalid_transmit_body")
            return
        if duid is None:
            if not self.remotes:
                emsg = "No remote to send to\n"
                console.terse(emsg)
                self.incStat("invalid_destination")
                return
            duid = self.remotes.values()[0].uid
        self.txMsgs.append((msg, duid))

    def  _handleOneTxMsg(self):
        '''
        Take one message from .txMsgs deque and handle it
        Assumes there is a message on the deque
        '''
        body, duid = self.txMsgs.popleft() # duple (body dict, destination uid
        self.message(body, duid)
        console.verbose("{0} sending\n{1}\n".format(self.name, body))

    def serviceTxMsgs(self):
        '''
        Service .txMsgs queue of outgoing  messages
        '''
        while self.txMsgs:
            self._handleOneTxMsg()

    def serviceTxMsgOnce(self):
        '''
        Service one message on .txMsgs queue of outgoing messages
        '''
        if self.txMsgs:
            self._handleOneTxMsg()

    def message(self, body, duid):
        '''
        Sends message body remote at duid
        '''
        pass

    def tx(self, packed, duid):
        '''
        Queue duple of (packed, da) on stack .txes queue
        Where da is the ip destination (host,port) address associated with
        the remote identified by duid
        '''
        if duid not in self.remotes:
            msg = "Invalid destination remote id '{0}'".format(duid)
            raise raeting.StackError(msg)
        self.txes.append((packed, self.remotes[duid].ha))


    def _handleOneTx(self, laters, blocks):
        '''
        Handle one message on .txes deque
        Assumes there is a message
        laters is deque of messages to try again later
        blocks is list of destinations that already blocked on this service
        '''
        tx, ta = self.txes.popleft()  # duple = (packet, destination address)

        if ta in blocks: # already blocked on this iteration
            laters.append((tx, ta)) # keep sequential
            return

        try:
            self.server.send(tx, ta)
        except socket.error as ex:
            if ex.errno == errno.EAGAIN or ex.errno == errno.EWOULDBLOCK:
                #busy with last message save it for later
                laters.append((tx, ta))
                blocks.append(ta)
            else:
                raise

    def serviceTxes(self):
        '''
        Service the .txes deque to send  messages through server
        '''
        if self.server:
            laters = deque()
            blocks = []
            while self.txes:
                self._handleOneTx(laters, blocks)
            while laters:
                self.txes.append(laters.popleft())

    def serviceTxOnce(self):
        '''
        Service on message on the .txes deque to send through server
        '''
        if self.server:
            laters = deque()
            blocks = [] # will always be empty since only once
            if self.txes:
                self._handleOneTx(laters, blocks)
            while laters:
                self.txes.append(laters.popleft())

    def serviceAllRx(self):
        '''
        Service:
           server receive
           rxes queue
           process
        '''
        self.serviceReceives()
        self.serviceRxes()
        self.process()

    def serviceAllTx(self):
        '''
        Service:
           txMsgs queue
           txes queue to server send
        '''
        self.serviceTxMsgs()
        self.serviceTxes()

    def serviceAll(self):
        '''
        Service or Process:
           server receive
           rxes queue
           process
           txMsgs queue
           txes queue to server send
        '''
        self.serviceAllRx()
        self.serviceAllTx()

    def serviceServer(self):
        '''
        Service the server's receive and transmit queues
        '''
        self.serviceReceives()
        self.serviceTxes()

    def serviceOneAllRx(self):
        '''
        Propagate one packet all the way through the received side of the stack
        Service:
           server receive
           rxes queue
           process
        '''
        self.serviceReceiveOnce()
        self.serviceRxOnce()
        self.process()

    def serviceOneAllTx(self):
        '''
        Propagate one packet all the way through the transmit side of the stack
        Service:
           txMsgs queue
           txes queue to server send
        '''
        self.serviceTxMsgOnce()
        self.serviceTxOnce()

    def process(self):
        '''
        Allow timer based processing
        '''
        pass



