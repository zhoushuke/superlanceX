#!/usr/bin/env python -u
# -*- coding: utf-8 -*-
##############################################################################
#
# Copyright (c) 2007 Agendaless Consulting and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the BSD-like license at
# http://www.repoze.org/LICENSE.txt.  A copy of the license should accompany
# this distribution.  THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL
# EXPRESS OR IMPLIED WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND
# FITNESS FOR A PARTICULAR PURPOSE
#
##############################################################################

# A event listener meant to be subscribed to PROCESS_STATE_CHANGE
# events.  It will send mail when processes that are children of
# supervisord transition unexpectedly to the EXITED state.

# A supervisor config snippet that tells supervisor to use this script
# as a listener is below.
#
# [eventlistener:crashmail]
# command =
#     /usr/bin/crashmail
#         -o hostname -a -m notify-on-crash@domain.com
#         -s '/usr/sbin/sendmail -t -i -f crash-notifier@domain.com'
# events=PROCESS_STATE
#
# Sendmail is used explicitly here so that we can specify the 'from' address.

doc = """\
crashmail.py [-p processname] [-a] [-o string] [-m mail_address]
             [-s sendmail] URL

Options:

-p -- specify a supervisor process_name.  Send mail when this process
      transitions to the EXITED state unexpectedly. If this process is
      part of a group, it can be specified using the
      'group_name:process_name' syntax.

-a -- Send mail when any child of the supervisord transitions
      unexpectedly to the EXITED state unexpectedly.  Overrides any -p
      parameters passed in the same crashmail process invocation.

-o -- Specify a parameter used as a prefix in the mail subject header.

-s -- the sendmail command to use to send email
      (e.g. "/usr/sbin/sendmail -t -i").  Must be a command which accepts
      header and message data on stdin and sends mail.  Default is
      "/usr/sbin/sendmail -t -i".

-m -- specify an email address.  The script will send mail to this
      address when crashmail detects a process crash.  If no email
      address is specified, email will not be sent.

The -p option may be specified more than once, allowing for
specification of multiple processes.  Specifying -a overrides any
selection of -p.

A sample invocation:

crashmail.py -p program1 -p group1:program2 -m dev@example.com

"""

import getopt
import os
import sys
import socket
import collections
from sendxmail import MailService
from supervisor import childutils


def usage(exitstatus=255):
    print(doc)
    sys.exit(exitstatus)


class CrashMail:

    def __init__(self, programs, any, email_host, email_to, optionalheader):

        self.programs = programs
        self.any = any
        self.email_host = email_host
        self.email_to = email_to
        self.optionalheader = optionalheader
        self.stdin = sys.stdin
        self.stdout = sys.stdout
        self.stderr = sys.stderr
        self.mailer = MailService(self.email_host)

    @staticmethod
    def get_host_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip

    def runforever(self, test = False):
        while 1:
            # we explicitly use self.stdin, self.stdout, and self.stderr
            # instead of sys.* so we can unit test this code
            headers, payload = childutils.listener.wait(
                self.stdin, self.stdout)

            if not headers['eventname'] == 'PROCESS_STATE_EXITED':
                # do nothing with non-TICK events
                childutils.listener.ok(self.stdout)
                if test:
                    self.stderr.write('non-exited event\n')
                    self.stderr.flush()
                    break
                continue

            pheaders, pdata = childutils.eventdata(payload+'\n')

            if int(pheaders['expected']):
                childutils.listener.ok(self.stdout)
                if test:
                    self.stderr.write('expected exit\n')
                    self.stderr.flush()
                    break
                continue

            # event timestamp
            event_timestamp = childutils.get_asctime()

            #get local ip:
            host_ip = self.get_host_ip()

            #process name
            process_name = pheaders['processname']

            #process pid
            process_pid = pheaders['pid']

            #group name
            group_name = pheaders['groupname']

            #from_state
            from_state = pheaders['from_state']

            #msg
            msg = 'Process %s in group %s exited unexpectedly (pid %s) from state %s' % (process_name,
                                                                                         group_name,
                                                                                         process_pid,
                                                                                         from_state)
            #html struct
            html_struct = collections.OrderedDict()
            html_struct['event_time'] = event_timestamp
            html_struct['host_ip'] = host_ip
            html_struct['process_name'] = process_name
            html_struct['process_pid'] = process_pid
            html_struct['event_msg'] = msg

            #subject
            subject = '%s in %s crashed at %s' % (process_name,
                                                  host_ip,
                                                  event_timestamp)

            if self.optionalheader:
                subject = self.optionalheader + ':' + subject

            self.stderr.write('unexpected exit, mailing\n')
            self.stderr.flush()

            #self.mail(self.email_to, subject, msg)
            self.send_mail_by_http(self.email_to, subject, html_struct)

            childutils.listener.ok(self.stdout)
            if test:
                break

    def mail(self, email_to, subject, msg):
        body = 'To: %s\n' % email_to
        body += 'Subject: %s\n' % subject
        body += '\n'
        body += msg
        with os.popen(self.sendmail, 'w') as m:
            m.write(body)
        self.stderr.write('Mailed:\n\n%s' % body)
        self.mailed = body

    def send_mail_by_http(self, email_to, subject, html_content):
        #-t: email to
        #-s: subject
        #-f: format: default: html
        #-c: html content
        body = self.mailer.gen_html_body(html_content)
        html = self.mailer.gen_html('Process Alert By Supervisor'.encode('utf-8'), body)
        self.mailer.send(email_to, subject.encode('utf-8'), 'html', html.encode('utf-8'))

        #try:
        #    subprocess.call('%s -t %s -s %s -f %s -c %s' % (self.sendmail, email, subject, "html", html_content),
        #                    shell = True)
        #except Exception as e:
        #    os._exit(1)


def main(argv=sys.argv):
    short_args = "hp:ao:h:m:"
    long_args = [
        "help",
        "program=",
        "any",
        "optionalheader=",
        "email_host="
        "email_to=",
        ]
    arguments = argv[1:]
    try:
        opts, args = getopt.getopt(arguments, short_args, long_args)
    except Exception:
        usage()

    programs = []
    any = False
    email_host = 'xxx.xxx.xxx.xxx:6789'
    email_to = 'senserealty-devops@sensetime.com'
    optionalheader = None

    for option, value in opts:

        if option in ('-h', '--help'):
            usage(exitstatus=0)

        if option in ('-p', '--program'):
            programs.append(value)

        if option in ('-a', '--any'):
            any = True

        if option in ('-h', '--email_host'):
            email_host = value

        if option in ('-m', '--email_to'):
            email_to = value

        if option in ('-o', '--optionalheader'):
            optionalheader = value

    if not 'SUPERVISOR_SERVER_URL' in os.environ:
        sys.stderr.write('crashmail must be run as a supervisor event '
                         'listener\n')
        sys.stderr.flush()
        return

    prog = CrashMail(programs, any, email_host, email_to, optionalheader)
    prog.runforever()


if __name__ == '__main__':
    main()

