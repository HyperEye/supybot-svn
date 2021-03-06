###
# Copyright (c) 2013, Nils Brinkmann
# All rights reserved.
#
#
###

import os
import cPickle
import time

import supybot.callbacks as callbacks
import supybot.conf as conf
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircmsgs as ircmsgs
import supybot.ircutils as ircutils
import supybot.schedule as schedule
import supybot.utils as utils

try:
    import pysvn
except ImportError:
    raise callbacks.Error, 'You need to have PySVN installed to use this ' \
                           'plugin.  Download it at <http://pysvn.tigris.org/>'

    
class Helper(object):
    @staticmethod
    def getHeadRev(url):
        client = pysvn.Client()
        infoList = client.info2(url, recurse=False)
        headInfo = infoList[0][1]
        headRev = headInfo.data["last_changed_rev"]
        return headRev
        
    @staticmethod
    def getHeadRevNum(url):
        return Helper.getHeadRev(url).number
        
    @staticmethod
    def getLogItemsByRange(url, startRevNum, endRevNum):
        #get the revisions and the log for them
        client = pysvn.Client()
        startRev = pysvn.Revision( pysvn.opt_revision_kind.number, startRevNum )
        endRev = pysvn.Revision( pysvn.opt_revision_kind.number, endRevNum )
        log = client.log(url, endRev, startRev, True)
        return log
        
    @staticmethod
    def getLastLogItems(url, numRevs):
        headRevNum = Helper.getHeadRevNum(url)
        if(headRevNum <= numRevs):
            startRevNum = 1
        else:
            startRevNum = headRevNum - numRevs
        return Helper.getLogItemsByRange(url, headRevNum, startRevNum)
        
    @staticmethod
    def logItemToString(logItem, reponame):
        #--> "\x02" for bold
        #--> "\x16" for italic/reverse
        #--> "\x1F" for underlined
        returnStr = ""
        if( 'revision' in logItem.data ):
            returnStr += "\x02[" + reponame + "]\x02 \x0304r" + str(logItem.revision.number) + "\x03 "
        #if( 'date' in logItem.data ):
        #    returnStr += time.strftime("%b-%d %H:%M", time.gmtime((logItem.date))) + " - "
        if( 'author' in logItem.data ):
            returnStr += "\x0303" + logItem.author + "\x03"
        if( 'changed_paths' in logItem.data and len(logItem.changed_paths) > 0 ):
            returnStr += " (" + str(len(logItem.changed_paths)) + " files changed"
            if( len(logItem.changed_paths) <= 6 ):
                returnStr += ":\x0310"
                for path in logItem.changed_paths:
                    returnStr += " " + path.path
                returnStr += "\x03"
            returnStr += ")"
        if( 'message' in logItem.data ):
            messages = logItem.message.split('\n')
            messages = filter(None, messages)
            returnStr += ": "
            for message in messages[:-1]:
                returnStr += message + " || "
            else:
                returnStr += messages[-1]
            
        return returnStr
        
    @staticmethod
    def splitLine(line, lineMax):
        for start in range(0, len(line), lineMax):
            yield line[start:start+lineMax]

class Notifier(object):
    def __init__(self, irc, channels, name, url):
        self.irc = irc #needed to write output to IRC
        self.channels = channels#.split(",")
        self.name = name
        self.url = url
        
        #To init the lastRev, check for the current HEAD, then
        # subtract 5 revisions from that, so that something can be shown.
        #That's better than showing nothing or than showing everything
        self.lastRev = Helper.getHeadRevNum(self.url) - 5
        if(self.lastRev < 0):
            self.lastRev = 0
        
    def check(self):
        headRev = Helper.getHeadRevNum(self.url)
        if(self.lastRev < headRev):
            self.lastRev = headRev #do this first, avoid that crashes lead to multiple output
            log = Helper.getLogItemsByRange(self.url, self.lastRev, headRev)
            for item in log:
                itemStr = Helper.logItemToString(item, self.name)
                for channel in self.channels:
                    for line in Helper.splitLine(itemStr, 400):
                        self.irc.queueMsg( ircmsgs.privmsg(channel, line) )

#This class is needed to create picklable objects of Notifier
#The IRC-instance in Notifier is not picklable (makes sense)
class NotifierConfig(object):
    def __init__(self, notifier):
        if(notifier == None):
            self.channels = []
            self.name = ""
            self.url = ""
        else:
            self.channels = notifier.channels
            self.name = notifier.name
            self.url = notifier.url
        
    def getNotifier(self, irc):
        return Notifier(irc, self.channels, self.name, self.url)            
        
        
class Subversion(callbacks.Plugin):
    """This plugin adds commands to gather information about a specific SVN repository."""
    
    def __init__(self, irc):
        self.__parent = super(Subversion, self)
        self.__parent.__init__(irc)
                
        #a dict is easier to search through
        #first: notifier.name
        #second: notifier
        self.notifiers = {};
        
        #read the notifiers from file
        #use NotifierConfig-objects for getting them
        filepath = conf.supybot.directories.data.dirize('Subversion.db')
        if( os.path.exists(filepath) ):
            try:
                notifierConfigs = cPickle.load( open( filepath, "rb" ) )
                for config in notifierConfigs:
                    self.notifiers[config.name] = config.getNotifier(irc)
            except EOFError as error:
                irc.reply("Error when trying to load existing data.")
                irc.reply("Message: " + str(error))
        
        #this adds the notifiers
        for name, notifier in self.notifiers.items():
            if( hasattr(irc, 'reply') ):
                irc.reply( "Adding notifier '" + notifier.name + "' from config" )
            self._addNotifier(irc, notifier)

    def die(self):
        #remove all the notifiers
        for key in self.notifiers.keys():
            try:
                schedule.removePeriodicEvent( self.notifiers[key].name )
            except KeyError:
                #this happens if the key is not there
                pass
                
        #pickle the notifiers to file
        #use notifierConfig-instaces for that
        try:
            filepath = conf.supybot.directories.data.dirize('Subversion.db')
            notifierConfigs = []
            for name, notifier in self.notifiers.items():
                notifierConfigs.append( NotifierConfig(notifier) )
            cPickle.dump( notifierConfigs, open( filepath, "wb" ) )
        except cPickle.PicklingError as error:
            print("More: Error when pickling to file...")
            print(error)
            
        #kill the rest of the plugin
        self.__parent.die()    
    
    def _addNotifier(self, irc, notifier):
        try:
            id = schedule.addPeriodicEvent(notifier.check, 60, notifier.name)
        except AssertionError:
            #this happens when the plugin was unloaded uncleanly
            #do nothing else, but add this event to the notifier list (so the user can remove it)
            irc.reply( "There already is a notifier called '" + notifier.name + "'" )
    
    def getheadrev(self, irc, msg, args, url):
        """<url>

        Returns the HEAD revision number of the given <url>
        """
        irc.reply( Helper.getHeadRevNum(url) )
    getheadrev = wrap(getheadrev, ['text'])
        
    def svnlog(self, irc, msg, args, url, range=5):
        """<url> [<range>]

        Returns the last log entries from the Repository of <url>.
        The <range> let's you set a specific range of entries returned, defaults to 5.
        """
        #get the revisions and the log for them
        log = Helper.getLastLogItems(url, range)
        
        #output the results
        for item in log:
            itemStr = Helper.logItemToString(item, "Subversion")
            irc.reply( itemStr )
    svnlog = wrap(svnlog, ['text', additional(('int', 'range'), 5)])
    
    def add(self, irc, msg, args, name, url, channels):
        """<name> <url> <channel1> [<channel2> ...]
        
        Adds a notifier with <name> of <url> to the given channels
        """
        
        #check if there is a notifier (do not add a second one, the scheduler does not allow that)
        if( name in self.notifiers ):
            irc.reply( "There already is a notifier called '" + name + "'" )
            return
        
        #needs to be printed before registering the event, because it will be executed immediately
        irc.reply( "Adding Subversion Notifier '" + name + "' to channels " + ", ".join(channels) + " with " + url )
        
        notifier = Notifier(irc, channels, name, url)
        self._addNotifier(irc, notifier)
        self.notifiers[name] = notifier
    add = wrap(add, ['somethingWithoutSpaces', 'somethingWithoutSpaces', many('validChannel')])
    
    
    def remove(self, irc, msg, args, name):
        """<name>
        
        Removes the notifier called <name>
        """
        if not(name in self.notifiers):
            irc.reply( "There is no notifier named '" + name + "'")
            return
        
        schedule.removePeriodicEvent(name)
        irc.reply( "Removed '" + name + "'")
        del self.notifiers[name]
    remove = wrap(remove, ['text'])
    
    def list(self, irc, msg, args, channel):
        """[<channel>]
        
        Lists all the Subversion notifiers
        Optionally posts the list in the given <channel>
        """
        if not( self.notifiers ):
            irc.reply( "No notifiers configured" )
            return
        
        for key, notifier in self.notifiers.items():
            output = ""
            output += ", ".join(notifier.channels) + " - "
            output += notifier.name + " - "
            output += notifier.url
            irc.reply( output )
    list = wrap(list, ['channel'])

Class = Subversion


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
