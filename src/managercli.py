#
# Subscription manager commandline utility. This script is a modified version of
# cp_client.py from candlepin scripts
#
# Copyright (c) 2010 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
#
# Red Hat trademarks are not licensed under GPLv2. No permission is
# granted to use or replicate Red Hat trademarks that are incorporated
# in this software or its documentation.
#

import os
import sys
import shutil
import config
import constants
import connection
import hwprobe
import optparse
import pprint
from optparse import OptionParser
from certlib import CertLib, ConsumerIdentity, ProductDirectory, EntitlementDirectory
import managerlib
import gettext
from facts import getFacts
from M2Crypto import X509

import gettext
_ = gettext.gettext

from logutil import getLogger
from httplib import socket
from socket import error as socket_error

log = getLogger(__name__)

cfg = config.initConfig()

def handle_exception(msg, ex):
    log.error(msg)
    log.exception(ex)
    if isinstance(ex, socket_error):
        print 'network error, unable to connect to server'
        sys.exit(-1)
    else:
        systemExit(-1, ex)

class CliCommand(object):
    """ Base class for all sub-commands. """
    def __init__(self, name="cli", usage=None, shortdesc=None,
            description=None):
        self.shortdesc = shortdesc
        if shortdesc is not None and description is None:
            description = shortdesc
        self.debug = 0
        self._cp = None
        self.new_cp = self.create_connection()
        self.cp = self.new_cp
        self.parser = OptionParser(usage=usage, description=description)
        self._add_common_options()
        self.name = name
        self.certlib = CertLib()

    def _add_common_options(self):
        """ Add options that apply to all sub-commands. """

        self.parser.add_option("--debug", dest="debug",
                default=0, help="debug level")

    def _do_command(self):
        pass

    # ditch all this, self.cp is a UEPConnection that kind figure out
    # where it needs to authtenticate
#    @property
#    def cp_Two(self):
#        if not self._cp:
#            if ConsumerIdentity.exists():
#                self._cp = self.create_connection_with_user_identity()
#            else:
#                self._cp = self.create_connection()
#        return self._cp

#    @cp.setter
#    def cp_Two(self, cp):
#        self._cp = cp

    def add_user_identity(self):
        cert_file = ConsumerIdentity.certpath()
        key_file = ConsumerIdentity.keypath()

        self.new_cp.add_ssl_certs(cert_file=cert_file, key_file=key_file)

        return connection.UEPConnection(host=cfg['hostname'] or "localhost",
                                           ssl_port=cfg['port'], handler="/candlepin", 
                                           cert_file=cert_file, key_file=key_file)
    def create_connection(self):
        return connection.UEPConnection(host=cfg['hostname'] or "localhost",
                                               ssl_port=cfg['port'], handler="/candlepin")

    def main(self):

        (self.options, self.args) = self.parser.parse_args()
        # we dont need argv[0] in this list...
        self.args = self.args[1:]
        # do the work, catch most common errors here:
        try:
            self._do_command()
        except X509.X509Error, e:
            log.error(e)
            print 'Consumer certificates corrupted. Please re-register'


class ReRegisterCommand(CliCommand):
    def __init__(self):
        usage = "usage: %prog reregister [OPTIONS]"
        shortdesc = "re-register the client to a Unified Entitlement Platform."
        desc = shortdesc

        CliCommand.__init__(self, "reregister", usage, shortdesc, desc)

        self.username = None
        self.password = None

        self.parser.add_option("--username", dest="username",
                               help="specify a username")
        self.parser.add_option("--password", dest="password",
                               help="specify a password")
        self.parser.add_option("--consumerid", dest="consumerid",
                               help="register to an existing consumer")

    def _validate_options(self):
        if not ConsumerIdentity.existsAndValid() and not (self.options.username and self.options.password):
            print (_("""Error: username and password are required to re-register. \nConsumer identity either does not exists or is corrupted. Try re-register --help."""))
            sys.exit(-1)

    def _do_command(self):

        self._validate_options()
        self.add_user_identity()

        if not ConsumerIdentity.existsAndValid() and not self.options.consumerid:
            #identity is corrupted, and if register is called it will fail saying consumer
            #already exists. So only way to force register to succeed is to delete the certs for now.
            #ugly!
            log.info("consumer identity is not valid and consumer id was not passed. Deleting old ones and calling register")
            shutil.rmtree("/etc/pki/consumer", ignore_errors=True)
            # this should REGISTER
            rc = RegisterCommand()
            rc.main()
            sys.exit(0)

        try:
            if self.options.consumerid:
                consumer = self.cp.getConsumerById(self.options.consumerid, self.options.username, self.options.password)
                managerlib.persist_consumer_cert(consumer)
            else:
                consumerid = check_registration()['uuid']
                if self.options.username and self.options.password:
                    print 'Ignoring username and password options. Using old uuid %s' % consumerid
                consumer = self.cp.regenIdCertificate(consumerid)
                managerlib.persist_consumer_cert(consumer)

            log.info("Successfully ReRegistered the client from Entitlement Platform.")
        except connection.RestlibException, re:
            log.exception(re)
            log.error("Error: Unable to ReRegister the system: %s" % re)
            systemExit(-1, re.msg)
        except Exception, e:
            handle_exception("Error: Unable to ReRegister the system", e)

class RegisterCommand(CliCommand):

    def __init__(self):
        usage = "usage: %prog register [OPTIONS]"
        shortdesc = "register the client to a Unified Entitlement Platform."
        desc = "register"

        CliCommand.__init__(self, "register", usage, shortdesc, desc)

        self.username = None
        self.password = None

        self.parser.add_option("--username", dest="username",
                               help="specify a username")
        self.parser.add_option("--type", dest="consumertype", default="system",
                               help="the type of consumer to create. Defaults to system")
        self.parser.add_option("--name", dest="consumername",
                               help="name of the consumer to create. Defaults to the username.")
        self.parser.add_option("--password", dest="password",
                               help="specify a password")
        self.parser.add_option("--consumerid", dest="consumerid",
                               help="register to an existing consumer")
        self.parser.add_option("--autosubscribe", action='store_true',
                               help="automatically subscribe this system to\
                                     compatible subscriptions.")
        self.parser.add_option("--force",  action='store_true', 
                               help="register the system even if it is already registered")

    def _validate_options(self):
        if not (self.options.username and self.options.password):
            print (_("Error: username and password are required to register, try register --help.\n"))
            sys.exit(-1)

        if ConsumerIdentity.exists() and not self.options.force:
            print(_("This system is already registered. Use --force to override"))
            sys.exit(1)

    def _do_command(self):
        """
        Executes the command.
        """
        self._validate_options()

        facts = getFacts()

        #Default the username if not there.
        consumername = self.options.consumername
        if consumername == None:
            consumername = self.options.username

        if self.options.consumerid:
            consumer = self.cp.getConsumerById(self.options.consumerid, self.options.username, self.options.password)

        elif ConsumerIdentity.exists() and self.options.force:
           try:
               consumer = self.cp.registerConsumer(self.options.username,
                       self.options.password, name=consumername, type=self.options.consumertype,
                       facts=facts.get_facts())
           except connection.RestlibException, re:
               log.exception(re)
               systemExit(-1, re.msg)

           if ConsumerIdentity.existsAndValid():
               consumerid = ConsumerIdentity.read().getConsumerId()
               try:
                   self.cp.unregisterConsumer(consumerid)
                   log.info("--force specified. Successfully unsubscribed the old consumer.")
               except:
                   log.error("Unable to unregister with consumer %s" % consumerid)
        else:
            try:
               consumer = self.cp.registerConsumer(self.options.username,
                       self.options.password, name=consumername, type=self.options.consumertype,
                       facts=facts.get_facts())
            except connection.RestlibException, re:
                log.exception(re)
                systemExit(-1, re.msg)

        managerlib.persist_consumer_cert(consumer)

        # instead of recreating cp here, lets just add an auth'ed connection to it
#        self.cp = self.create_connection_with_user_identity()
        self.add_user_identity()
        if self.options.autosubscribe:
            # try to auomatically bind products
            for pname, phash in managerlib.getInstalledProductHashMap().items():
                try:
                   self.cp.bindByProduct(consumer['uuid'], phash)
                   print "Bind Product ", pname,phash
                   log.info("Automatically subscribe the machine to product %s " % pname)
                except:
                   log.warning("Warning: Unable to auto subscribe the machine to %s" % pname)
            self.certlib.update()

class UnRegisterCommand(CliCommand):
    def __init__(self):
        usage = "usage: %prog unregister [OPTIONS]"
        shortdesc = "unregister the client from a Unified Entitlement Platform."
        desc = "unregister"

        CliCommand.__init__(self, "unregister", usage, shortdesc, desc)

    def _validate_options(self):
        pass

    def _do_command(self):
        self.add_user_identity()
        if not ConsumerIdentity.exists():
            print(_("This system is currently not registered."))
            sys.exit(1)

        try:
           consumer = check_registration()['uuid']
           self.cp.unregisterConsumer(consumer)
           # clean up stale consumer/entitlement certs
           shutil.rmtree("/etc/pki/consumer/", ignore_errors=True)
           shutil.rmtree("/etc/pki/entitlement/", ignore_errors=True)
           log.info("Successfully Unsubscribed the client from Entitlement Platform.")
        except connection.RestlibException, re:
           log.exception(re)
           log.error("Error: Unable to UnRegister the system: %s" % re)
           systemExit(-1, re.msg)
        except Exception, e:
            handle_exception("Error: Unable to UnRegister the system", e)

class SubscribeCommand(CliCommand):
    def __init__(self):
        usage = "usage: %prog subscribe [OPTIONS]"
        shortdesc = "subscribe the registered user to a specified product or regtoken."
        desc = "subscribe"
        CliCommand.__init__(self, "subscribe", usage, shortdesc, desc)

        self.product = None
        self.regtoken = None
        self.substoken = None
        self.parser.add_option("--regtoken", dest="regtoken", action='append',
                               help="regtoken")
        self.parser.add_option("--pool", dest="pool", action='append',
                               help="subscription pool id")
        self.parser.add_option("--email", dest="email", action='store',
                               help=_("optional email address to notify when "
                               "token activation is complete. Used with "
                               "--regtoken only"))
        self.parser.add_option("--locale", dest="locale", action='store',
                               help=_("optional language to use for email "
                               "notification when token activation is "
                               "complete. Used with --regtoken and --email "
                               "only. Examples: en-us, de-de"))
        self.add_user_identity()

    def _validate_options(self):
        if not (self.options.regtoken or self.options.pool):
            print _("Error: Need either --pool or --regtoken, Try subscribe --help")
            sys.exit(-1)

    def _do_command(self):
        """
        Executes the command.
        """
        self._validate_options()
        consumer = check_registration()['uuid']
        try:
            # update facts first, if we need to
            facts = getFacts()

            if facts.delta():
                self.cp.updateConsumerFacts(consumer, facts.get_facts())
            

            if self.options.regtoken:
                for regnum in self.options.regtoken:
                    bundles = self.cp.bindByRegNumber(consumer, regnum,
                            self.options.email, self.options.locale)
                    log.info("Info: Successfully subscribed the machine to registration token %s" % regnum)

            if self.options.pool:
                for pool in self.options.pool:
                    try:
                        bundles = self.cp.bindByEntitlementPool(consumer, pool)
                        log.info("Info: Successfully subscribed the machine the Entitlement Pool %s" % pool)
                    except connection.RestlibException, re:
                        log.exception(re)
                        if re.code == 403:
                            print re.msg  #already subscribed.
                        elif re.code == 400:
                            print re.msg #no such pool.
                        else:
                            systemExit(-1, re.msg) #some other error.. don't try again

            self.certlib.update()
        except Exception, e:
            handle_exception("Unable to subscribe: %s" % e, e)

class UnSubscribeCommand(CliCommand):
    def __init__(self):
        usage = "usage: %prog unsubscribe [OPTIONS]"
        shortdesc = "unsubscribe the registered user from all or specific subscriptions."
        desc = "unsubscribe"
        CliCommand.__init__(self, "unsubscribe", usage, shortdesc, desc)

        self.serial_numbers = None
        self.parser.add_option("--serial", dest="serial",
                               help="Certificate serial to unsubscribe")

    def _validate_options(self):
        CliCommand._validate_options(self)

    def _do_command(self):
        """
        Executes the command.
        """
        self.add_user_identity()
        consumer = check_registration()['uuid']
        try:
            if self.options.serial:
                self.cp.unBindBySerialNumber(consumer, self.options.serial)
                log.info("This machine has been Unsubscribed from subcription with Serial number %s" % (self.options.serial))
            else:
                self.cp.unbindAll(consumer)
                log.info("Warning: This machine has been unsubscribed from all its subscriptions as per user request.")
            self.certlib.update()
        except connection.RestlibException, re:
            log.error(re)
            systemExit(-1, re.msg)
        except Exception,e:
            handle_exception("Unable to perform unsubscribe due to the following exception \n Error: %s" % e, e)

class FactsCommand(CliCommand):
    def __init__(self):
        usage = "usage: %prog facts [OPTIONS]"
        shortdesc = "show information for facts"
        desc = "facts"
        CliCommand.__init__(self, "facts", usage, shortdesc, desc)
        
        self.parser.add_option("--list", action="store_true",
                               help="list known facts for this system")
        self.parser.add_option("--update", action="store_true",
                               help="update the system facts")

    def _validate_options(self):
        # one or the other
        CliCommand._validate_options(self)

    def _do_command(self):
        self.add_user_identity()
        if self.options.list:
            facts = getFacts()
            fact_dict = facts.get_facts()
            fact_keys = fact_dict.keys()
            fact_keys.sort()
            for key in fact_keys:
                print "%s: %s" % (key, fact_dict[key])

        if self.options.update:
            facts = getFacts()
            consumer = check_registration()['uuid']
            print consumer
            self.cp.updateConsumerFacts(consumer, facts.get_facts())

class ListCommand(CliCommand):
    def __init__(self):
        usage = "usage: %prog list [OPTIONS]"
        shortdesc = "list available or consumer subscriptions for registered user"
        desc = "list available or consumed Entitlement Pools for this system."
        CliCommand.__init__(self, "list", usage, shortdesc, desc)
        self.available = None
        self.consumed = None
        self.parser.add_option("--available", action='store_true',
                               help="available")
        self.parser.add_option("--consumed", action='store_true',
                               help="consumed")
        self.parser.add_option("--all", action='store_true',
                               help="if supplied with --available then all subscriptions are returned")                               


    def _validate_options(self):
        if (self.options.all and not self.options.available):
            print _("Error: --all is only applicable with --available")
            sys.exit(-1)

    def _do_command(self):
        """
        Executes the command.
        """

        self._validate_options()
        self.add_user_identity()
        
        consumer = check_registration()['uuid']
        if not (self.options.available or self.options.consumed):
           iproducts = managerlib.getInstalledProductStatus()
           if not len(iproducts):
               print("No installed Products to list")
               sys.exit(0)
           print """+-------------------------------------------+\n    Installed Product Status\n+-------------------------------------------+"""
           for product in iproducts:
               print constants.installed_product_status % product

        if self.options.available:
           if self.options.all:
               epools = managerlib.getAllAvailableSubscriptions(self.cp, consumer)
           else:
               epools = managerlib.getAvailableEntitlementsCLI(self.cp, consumer)
           if not len(epools):
               print("No Available subscription pools to list")
               sys.exit(0)
           print "+-------------------------------------------+\n    %s\n+-------------------------------------------+\n" % _("Available Subscriptions")
           for data in epools:
               # TODO:  Something about these magic numbers!
               product_name = self._format_name(data['productName'], 24, 80)

               print constants.available_subs_list % (product_name, 
                                                      data['productId'], 
                                                      data['id'], 
                                                      data['quantity'], 
                                                      data['endDate'])
            
        if self.options.consumed:
           cpents = managerlib.getConsumedProductEntitlements()
           if not len(cpents):
               print("No Consumed subscription pools to list")
               sys.exit(0)
           print """+-------------------------------------------+\n    Consumed Product Subscriptions\n+-------------------------------------------+\n"""
           for product in cpents:
                print constants.consumed_subs_list % product 

    def _format_name(self, name, indent, max_length):
        """
        Formats a potentially long name for multi-line display, giving
        it a columned effect.
        """
        words = name.split()
        current = indent
        lines = []
        line = [words.pop(0)]

        def add_line():
            lines.append(' '.join(line))

        # Split here and build it back up by word, this way we get word wrapping
        for word in words:
            if current + len(word) < max_length:
                current += len(word) + 1  # Have to account for the extra space
                line.append(word)
            else:
                add_line()
                line = [' ' * (indent - 1), word]
                current = indent

        add_line()
        return '\n'.join(lines)


# taken wholseale from rho...
class CLI:
    def __init__(self):

        self.cli_commands = {}
        for clazz in [ RegisterCommand, UnRegisterCommand, ListCommand, SubscribeCommand,\
                       UnSubscribeCommand, FactsCommand, ReRegisterCommand]:
            cmd = clazz()
            # ignore the base class
            if cmd.name != "cli":
                self.cli_commands[cmd.name] = cmd 


    def _add_command(self, cmd):
        self.cli_commands[cmd.name] = cmd

    def _usage(self):
        print "\nUsage: %s [options] MODULENAME --help\n" % os.path.basename(sys.argv[0])
        print "Supported modules:\n"

        # want the output sorted
        items = self.cli_commands.items()
        items.sort()
        for (name, cmd) in items:
            print("\t%-14s %-25s" % (name, cmd.shortdesc))
            #print(" %-25s" % cmd.parser.print_help())
        print("")

    def _find_best_match(self, args):
        """
        Returns the subcommand class that best matches the subcommand specified
        in the argument list. For example, if you have two commands that start
        with auth, 'auth show' and 'auth'. Passing in auth show will match
        'auth show' not auth. If there is no 'auth show', it tries to find
        'auth'.

        This function ignores the arguments which begin with --
        """
        possiblecmd = []
        for arg in args[1:]:
            if not arg.startswith("-"):
                possiblecmd.append(arg)

        if not possiblecmd:
            return None

        cmd = None
        key = " ".join(possiblecmd)
        if self.cli_commands.has_key(" ".join(possiblecmd)):
            cmd = self.cli_commands[key]

        i = -1
        while cmd == None:
            key = " ".join(possiblecmd[:i])
            if key is None or key == "":
                break

            if self.cli_commands.has_key(key):
                cmd = self.cli_commands[key]
            i -= 1

        return cmd

    def main(self):
        if len(sys.argv) < 2 or not self._find_best_match(sys.argv):
            self._usage()
            sys.exit(0)

        cmd = self._find_best_match(sys.argv)
        if not cmd:
            self._usage()
            sys.exit(0)

        cmd.main()

def systemExit(code, msgs=None):
    "Exit with a code and optional message(s). Saved a few lines of code."

    if msgs:
        if type(msgs) not in [type([]), type(())]:
            msgs = (msgs, )
        for msg in msgs:
            sys.stderr.write(unicode(msg).encode("utf-8") + '\n')
    sys.exit(code)

def check_registration():
    if not ConsumerIdentity.exists():
        needToRegister = \
            _("Error: You need to register this system by running " \
            "`register` command before using this option.")
        print needToRegister
        sys.exit(1)
    consumer = ConsumerIdentity.read()
    consumer_info = {"consumer_name" : consumer.getConsumerName(),
                     "uuid" : consumer.getConsumerId()
                    }
    return consumer_info

if __name__ == "__main__":
    CLI().main()
