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

from .. import raeting
from .. import nacling
from .. import stacking
from . import keeping
from . import packeting
from . import estating
from . import transacting

from ioflo.base.consoling import getConsole
console = getConsole()

class RoadStack(stacking.Stack):
    '''
    RAET protocol RoadStack for UDP communications. This is the primary
    network communication system in RAET. A stack does not work in the
    same way as a socket, instead transmit (tx) and recive (rx) lists become
    populated or emptied when calls are made to the transmit and recieve
    methods.

    name
        The name to give the stack, if no name is given it will be
        automatically assigned
    main
        <Incomplete Doc>
    keep
        Pass in a keep object, this object can define how stack data is
        persisted to disk
    dirpath
        The location on the filesystem to use for stack cacheing
    eid
        The estate id, if None is specified a default will be assigned
    ha
        The host address, this is a tuple of (network_addr, port) that will
        be bound to by the stack
    bufcnt
        The number of messages to buffer, defaults to 2
    safe
        Pass in a safe object to manage the encryption keys, RAET ships with
        the raet.road.keeping.SafeKeep class for simple management
    auto
        <Incomplete Doc>
    period
        The default itteration timeframe to use for the background management
        of the presence system. Defaults to 1.0
    '''
    Count = 0
    Eid = 1 # class attribute
    Hk = raeting.headKinds.raet # stack default
    Bk = raeting.bodyKinds.json # stack default
    Fk = raeting.footKinds.nacl # stack default
    Ck = raeting.coatKinds.nacl # stack default
    Bf = False # stack default for bcstflag
    Wf = False # stack default for waitflag
    Period = 1.0 # stack default for keep alive
    Offset = 0.5 # stack default for keep alive

    def __init__(self,
                 name='',
                 main=None,
                 keep=None,
                 dirpath='',
                 basedirpath='',
                 local=None,
                 localname ='', #local estate name
                 eid=None, #local estate eid
                 ha=("", raeting.RAET_PORT),
                 bufcnt=2,
                 safe=None,
                 auto=None,
                 period=None,
                 offset=None,
                 **kwa
                 ):
        '''
        Setup StackUdp instance

        stack.name and stack.local.name will match
        '''
        self.neid = self.Eid # eid of initial next estate to add to road

        if not name:
            name = "road{0}".format(RoadStack.Count)
            RoadStack.Count += 1

        if not keep:
            keep = keeping.RoadKeep(dirpath=dirpath,
                                    basedirpath=basedirpath,
                                    stackname=name)

        if not safe:
            safe = keeping.SafeKeep(dirpath=dirpath,
                                    basedirpath=basedirpath,
                                    stackname=name,
                                    auto=auto)
        self.safe = safe

        if not local:
            self.remotes = odict()
            local = estating.LocalEstate(stack=self,
                                         name=localname,
                                         eid=eid,
                                         main=main,
                                         ha=ha)
        else:
            if main is not None:
                local.main = True if main else False

        self.period = period if period is not None else self.Period
        self.offset = offset if offset is not None else self.Offset

        super(RoadStack, self).__init__(name=name,
                                        keep=keep,
                                        dirpath=dirpath,
                                        basedirpath=basedirpath,
                                        local=local,
                                        localname=localname,
                                        bufcnt=bufcnt,
                                        **kwa)

        self.transactions = odict() #transactions

    def nextEid(self):
        '''
        Generates next estate id number.
        '''
        self.neid += 1
        if self.neid > 0xffffffffL:
            self.neid = 1  # rollover to 1
        return self.neid

    def serverFromLocal(self):
        '''
        Create server from local data
        '''
        if not self.local:
            return None

        server = aiding.SocketUdpNb(ha=self.local.ha,
                        bufsize=raeting.UDP_MAX_PACKET_SIZE * self.bufcnt)
        return server

    def addRemote(self, remote, uid=None):
        '''
        Add a remote  to .remotes
        '''
        super(RoadStack, self).addRemote(remote, uid)
        if remote.timer.store is not self.store:
            raise raeting.StackError("Store reference mismatch between remote"
                    " '{0}' and stack '{1}'".format(remote.name, stack.name))

    def removeRemote(self, uid):
        '''
        Remove remote at key uid
        '''
        remote = self.remotes.get(uid)
        super(RoadStack, self).removeRemote(uid)
        if remote:
            for index in remote.indexes:
                if index in self.transactions:
                    self.transactions[index].nack()
                    self.removeTransaction(index)

    def fetchRemoteByHostPort(self, host, port):
        '''
        Search for remote with matching host and port
        Return remote if found Otherwise return None
        '''
        for remote in self.remotes.values():
            if remote.host == host and remote.port == port:
                return remote

        return None

    def fetchRemoteByHa(self, ha):
        '''
        Search for remote with matching host address tuple, ha = (host, port)
        Return remote if found Otherwise return None
        '''
        for remote in self.remotes.values():
            if remote.ha == ha:
                return remote

        return None

    def fetchRemoteByKeys(self, sighex, prihex):
        '''
        Search for remote with matching (name, sighex, prihex)
        Return remote if found Otherwise return None
        '''
        for remote in self.remotes.values():
            if (remote.signer.keyhex == sighex or
                remote.priver.keyhex == prihex):
                return remote

        return None

    def retrieveRemote(self, duid, ha=None):
        '''
        If duid is not None Then returns remote at duid if exists or None
        If duid is None Then uses first remote unless no remotes then creates one
           with ha or default if ha is None
        '''
        if duid is None:
            if not self.remotes: # no remote estate so make one
                if self.local.main:
                    dha = ('127.0.0.1', raeting.RAET_TEST_PORT)
                else:
                    dha = ('127.0.0.1', raeting.RAET_PORT)
                remote = estating.RemoteEstate(stack=self,
                                               eid=0,
                                               ha=ha if ha is not None else dha,
                                               period=self.period,
                                               offset=self.offset)
                self.addRemote(remote)
            duid = self.remotes.values()[0].uid # zeroth is default
        return (self.remotes.get(duid, None))

    def dumpLocal(self):
        '''
        Dump keeps of local estate
        '''
        self.keep.dumpLocal(self.local)
        self.safe.dumpLocal(self.local)

    def loadLocal(self, local=None, name=''):
        '''
        Load local estate if keeps found and verified
        otherwise use local if provided
        otherwise create default local
        '''
        keepData = self.keep.loadLocalData()
        safeData = self.safe.loadLocalData()
        if (keepData and self.keep.verifyLocalData(keepData) and
                safeData and self.safe.verifyLocalData(safeData)):
            self.local = estating.LocalEstate(stack=self,
                                          eid=keepData['uid'],
                                          name=keepData['name'],
                                          main=keepData['main'],
                                          ha=keepData['ha'],
                                          sid=keepData['sid'],
                                          sigkey=safeData['sighex'],
                                          prikey=safeData['prihex'],)
            self.safe.auto = safeData['auto']
            self.name = keepData['stack']
            self.neid = keepData['neid']

        elif local:
            local.stack = self
            self.local = local

        else:
            self.local = estating.LocalEstate(stack=self, name=name)

    def clearLocal(self):
        '''
        Clear local keeps
        '''
        super(RoadStack, self).clearLocal()
        self.safe.clearLocalData()

    def dumpRemote(self, remote):
        '''
        Dump keeps of remote estate
        '''
        self.keep.dumpRemote(remote)
        self.safe.dumpRemote(remote)

    def loadRemotes(self):
        '''
        Load .remotes from valid keep and safe data if any
        '''
        keeps = self.keep.loadAllRemoteData()
        safes = self.safe.loadAllRemoteData()
        if not keeps or not safes:
            return
        for key, keepData in keeps.items():
            if key not in safes:
                continue
            safeData = safes[key]
            if (not self.keep.verifyRemoteData(keepData) or not
                    self.safe.verifyRemoteData(safeData)):
                continue
            remote = estating.RemoteEstate(stack=self,
                                           eid=keepData['uid'],
                                           name=keepData['name'],
                                           ha=keepData['ha'],
                                           sid=keepData['sid'],
                                           acceptance=safeData['acceptance'],
                                           verkey=safeData['verhex'],
                                           pubkey=safeData['pubhex'],
                                           period=self.period,
                                           offset=self.offset)
            self.addRemote(remote)

    def clearRemote(self, remote):
        '''
        Clear remote keeps of remote estate
        '''
        super(RoadStack, self).clearRemote(remote)
        self.safe.clearRemote(remote)

    def clearRemoteKeeps(self):
        '''
        Clear all remote keeps
        '''
        super(RoadStack, self).clearRemoteKeeps()
        self.safe.clearAllRemoteData()

    def manage(self, cascade=False, immediate=False):
        '''
        Manage remote estates. Time based processing of remote status such as
        presence (keep alive) etc.

        cascade induces the alive transactions to run join, allow, alive until
        failure or alive success

        immediate indicates to run first attempt immediately and not wait for timer
        '''
        for remote in self.remotes.values(): # should not start anything
            remote.manage(cascade=cascade, immediate=immediate)

    def addTransaction(self, index, transaction):
        '''
        Safely add transaction at index If not already there
        '''
        self.transactions[index] = transaction
        re = index[2]
        remote = None
        if re in self.remotes:
            remote = self.remotes[re]
        else: # may be bootstrapping onto channel so using ha in index but 0th
            remote = self.fetchRemoteByHa(ha=re)
        if remote is not None:
            remote.indexes.add(index)

        console.verbose( "Added {0} transaction to {1} at '{2}'\n".format(
                transaction.__class__.__name__, self.name, index))

    def removeTransaction(self, index, transaction=None):
        '''
        Safely remove transaction at index If transaction identity same
        If transaction is None then remove without comparing identity
        '''
        if index in self.transactions:
            if transaction:
                if transaction is self.transactions[index]:
                    del  self.transactions[index]
            else:
                del self.transactions[index]

            re = index[2]
            remote = None
            if re in self.remotes:
                remote = self.remotes[re]
            else: # may be bootstrapping onto channel so using ha in index but 0th
                remote = self.fetchRemoteByHa(ha=re)
            if remote is not None:
                remote.indexes.discard(index)

    def _handleOneRx(self):
        '''
        Handle on message from .rxes deque
        Assumes that there is a message on the .rxes deque
        '''
        raw, sa, da = self.rxes.popleft()
        console.verbose("{0} received packet\n{1}\n".format(self.name, raw))

        packet = packeting.RxPacket(stack=self, packed=raw)
        try:
            packet.parseOuter()
        except raeting.PacketError as ex:
            console.terse(str(ex) + '\n')
            self.incStat('parsing_outer_error')
            return

        sh, sp = sa
        dh, dp = da
        packet.data.update(sh=sh, sp=sp, dh=dh, dp=dp)

        deid = packet.data['de']
        if deid != 0 and self.local.uid != 0 and deid != self.local.uid:
            emsg = "Invalid destination eid = {0}. Dropping packet...\n".format(deid)
            console.concise( emsg)
            self.incStat('invalid_destination')
            return

        self.processRx(packet)

    def processRx(self, received):
        '''
        Process packet via associated transaction or
        reply with new correspondent transaction
        '''
        console.verbose("{0} received packet data\n{1}\n".format(self.name, received.data))
        console.verbose("{0} received packet index = '{1}'\n".format(self.name, received.index))

        cf = received.data['cf']
        rsid = received.data['si']
        remote = self.remotes.get(received.data['se'], None)

        if rsid == 0:
            if received.data['tk'] != raeting.trnsKinds.join:
                emsg = "Invalid sid '{0}' in packet\n".format(rsid)
                console.terse(emsg)
                self.incStat('invalid_sid_attempt')
                return

        else: # rsid !=0
            if remote and not cf: # packet from remote initiated transaction
                if not remote.validRsid(rsid): # invalid rsid
                    emsg = "Stale sid '{0}' in packet\n".format(rsid)
                    console.terse(emsg)
                    self.incStat('stale_sid_attempt')
                    #self.replyStale(received) need correspondent stalent to nack
                    return # should nack stale transaction

                if rsid != remote.rsid:
                    #console.verbose("Changing rsid of '{0}' from {1} to {2} in {3} "
                            #"transaction.\n".format(remote.name,
                                                    #remote.rsid,
                                                    #rsid,
                                                    #raeting.TRNS_KIND_NAMES[received.data['tk']]))
                    remote.rsid = rsid
                    # need to remove any stale correspondent transactions with this remote with older sid

        trans = self.transactions.get(received.index, None)
        if trans:
            trans.receive(received)
            return

        if cf: #packet from correspondent to non-existent locally initiated transaction
            self.stale(received)
            return

        self.reply(received, remote) # new transaction initiated by remote

    def reply(self, packet, remote):
        '''
        Reply to packet with corresponding transaction or action
        '''
        if (packet.data['tk'] == raeting.trnsKinds.join and
                packet.data['pk'] == raeting.pcktKinds.request and
                packet.data['si'] == 0):
            self.replyJoin(packet, remote)
            return

        if not remote:
            emsg = "Invalid remote destination estate id '{0}'\n".format(packet.data['se'])
            console.terse(emsg)
            self.stack.incStat('invalid_remote_eid')
            self.remove()
            return

        if (packet.data['tk'] == raeting.trnsKinds.allow and
                packet.data['pk'] == raeting.pcktKinds.hello and
                packet.data['si'] != 0):
            self.replyAllow(packet, remote)
            return

        if (packet.data['tk'] == raeting.trnsKinds.alive and
                packet.data['pk'] == raeting.pcktKinds.request and
                packet.data['si'] != 0):
            self.replyAlive(packet, remote)
            return

        if (packet.data['tk'] == raeting.trnsKinds.message and
                packet.data['pk'] == raeting.pcktKinds.message and
                packet.data['si'] != 0):
            self.replyMessage(packet, remote)
            return

        self.incStat('stale_packet')

    def process(self):
        '''
        Call .process or all transactions to allow timer based processing
        '''
        for transaction in self.transactions.values():
            transaction.process()

    def parseInner(self, packet):
        '''
        Parse inner of packet and return
        Assume all drop checks done
        '''
        try:
            packet.parseInner()
            console.verbose("{0} received packet body\n{1}\n".format(self.name, packet.body.data))
        except raeting.PacketError as ex:
            console.terse(str(ex) + '\n')
            self.incStat('parsing_inner_error')
            return None
        return packet

    def stale(self, packet):
        '''
        Initiate stale transaction in order to nack a stale correspondent packet
        '''
        duid = packet.data['se']
        ha = (packet.data['sh'], packet.data['sp'])
        remote = self.retrieveRemote(duid=duid, ha=ha)
        if not remote:
            emsg = "Invalid remote destination estate id '{0}'\n".format(duid)
            console.terse(emsg)
            self.incStat('invalid_remote_eid')
            return
        data = odict(hk=self.Hk, bk=self.Bk)
        staler = transacting.Staler(stack=self,
                                    remote=remote,
                                    kind=packet.data['tk'],
                                    sid=packet.data['si'],
                                    tid=packet.data['ti'],
                                    txData=data,
                                    rxPacket=packet)
        staler.nack()

    def join(self, duid=None, ha=None, timeout=None, cascade=False):
        '''
        Initiate join transaction
        '''
        remote = self.retrieveRemote(duid=duid, ha=ha)
        if not remote:
            emsg = "Invalid remote destination estate id '{0}'\n".format(duid)
            console.terse(emsg)
            self.incStat('invalid_remote_eid')
            return
        data = odict(hk=self.Hk, bk=self.Bk)
        joiner = transacting.Joiner(stack=self,
                                    remote=remote,
                                    timeout=timeout,
                                    txData=data,
                                    cascade=cascade)
        joiner.join()

    def replyJoin(self, packet, remote):
        '''
        Correspond to new join transaction
        '''
        data = odict(hk=self.Hk, bk=self.Bk)
        joinent = transacting.Joinent(stack=self,
                                      remote=remote,
                                      sid=packet.data['si'],
                                      tid=packet.data['ti'],
                                      txData=data,
                                      rxPacket=packet)
        joinent.join() # may assing joinent.remote here

    def allow(self, duid=None, ha=None, timeout=None, cascade=False):
        '''
        Initiate allow transaction
        '''
        remote = self.retrieveRemote(duid=duid, ha=ha)
        if not remote:
            emsg = "Invalid remote destination estate id '{0}'\n".format(duid)
            console.terse(emsg)
            self.incStat('invalid_remote_eid')
            return
        data = odict(hk=self.Hk, bk=raeting.bodyKinds.raw, fk=self.Fk)
        allower = transacting.Allower(stack=self,
                                      remote=remote,
                                      timeout=timeout,
                                      txData=data,
                                      cascade=cascade)
        allower.hello()

    def replyAllow(self, packet, remote):
        '''
        Correspond to new allow transaction
        '''
        data = odict(hk=self.Hk, bk=raeting.bodyKinds.raw, fk=self.Fk)
        allowent = transacting.Allowent(stack=self,
                                        remote=remote,
                                        sid=packet.data['si'],
                                        tid=packet.data['ti'],
                                        txData=data,
                                        rxPacket=packet)
        allowent.hello()

    def alive(self, duid=None,  ha=None, timeout=None, cascade=False):
        '''
        Initiate alive transaction
        If duid is None then create remote at ha
        '''
        remote = self.retrieveRemote(duid=duid, ha=ha)
        if not remote:
            emsg = "Invalid remote destination estate id '{0}'\n".format(duid)
            console.terse(emsg)
            self.incStat('invalid_remote_eid')
            return
        data = odict(hk=self.Hk, bk=self.Bk, fk=self.Fk, ck=self.Ck)
        aliver = transacting.Aliver(stack=self,
                                    remote=remote,
                                    timeout=timeout,
                                    txData=data,
                                    cascade=cascade)
        aliver.alive()

    def replyAlive(self, packet, remote):
        '''
        Correspond to new Alive transaction
        '''
        data = odict(hk=self.Hk, bk=self.Bk, fk=self.Fk, ck=self.Ck)
        alivent = transacting.Alivent(stack=self,
                                      remote=remote,
                                      bcst=packet.data['bf'],
                                      sid=packet.data['si'],
                                      tid=packet.data['ti'],
                                      txData=data,
                                      rxPacket=packet)
        alivent.alive()

    def message(self, body=None, duid=None, ha=None):
        '''
        Initiate message transaction to remote at duid
        If duid is None then create remote at ha
        '''
        remote = self.retrieveRemote(duid=duid, ha=ha)
        if not remote:
            emsg = "Invalid remote destination estate id '{0}'\n".format(duid)
            console.terse(emsg)
            self.incStat('invalid_remote_eid')
            return
        data = odict(hk=self.Hk, bk=self.Bk, fk=self.Fk, ck=self.Ck)
        messenger = transacting.Messenger(stack=self,
                                          remote=remote,
                                          txData=data,
                                          bcst=self.Bf,
                                          wait=self.Wf)
        messenger.message(body)

    def replyMessage(self, packet, remote):
        '''
        Correspond to new Message transaction
        '''
        data = odict(hk=self.Hk, bk=self.Bk, fk=self.Fk, ck=self.Ck)
        messengent = transacting.Messengent(stack=self,
                                            remote=remote,
                                            bcst=packet.data['bf'],
                                            sid=packet.data['si'],
                                            tid=packet.data['ti'],
                                            txData=data,
                                            rxPacket=packet)
        messengent.message()

