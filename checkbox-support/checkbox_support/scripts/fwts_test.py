#! /usr/bin/python3

import sys
import re
from time import time
from argparse import ArgumentParser, RawTextHelpFormatter, REMAINDER
from subprocess import Popen, PIPE
from syslog import syslog, LOG_INFO
from shutil import which
import os

# These tests require user interaction and need either special handling
# or skipping altogether (right now, we skip them but they're kept here
# in case we figure out a way to present the interaction to the user).
INTERACTIVE_TESTS = ['ac_adapter',
                     'battery',
                     'hotkey',
                     'power_button',
                     'brightness',
                     'lid']
# Tests recommended by the Hardware Enablement Team (HWE)
# These are performed on QA certification runs
QA_TESTS = ['acpitests',
            'apicedge',
            'aspm',
            'cpufreq',
            'dmicheck',
            'esrt',
            'klog',
            'maxfreq',
            'msr',
            'mtrr',
            'nx',
            'oops',
            'uefibootpath',
            'uefirtmisc',
            'uefirttime',
            'uefirtvariable',
            'version',
            'virt']
# The following tests will record logs in a separate file for the HWE team
HWE_TESTS = ['version',
             'mtrr',
             'virt',
             'apicedge',
             'klog',
             'oops']
# By default, we launch all the tests
TESTS = sorted(list(set(QA_TESTS + HWE_TESTS)))


def get_sleep_times(start_marker, end_marker, sleep_time, resume_time):
    logfile = '/var/log/syslog'
    log_fh = open(logfile, 'r', encoding='UTF-8')
    line = ''
    run = 'FAIL'
    sleep_start_time = 0.0
    sleep_end_time = 0.0
    resume_start_time = 0.0
    resume_end_time = 0.0

    while start_marker not in line:
        try:
            line = log_fh.readline()
        except UnicodeDecodeError:
            continue
        if start_marker in line:
            loglist = log_fh.readlines()

    for idx in range(0, len(loglist)):
        if 'PM: Syncing filesystems' in loglist[idx]:
            sleep_start_time = re.split('[\[\]]', loglist[idx])[1].strip()
        if 'ACPI: Low-level resume complete' in loglist[idx]:
            sleep_end_time = re.split('[\[\]]', loglist[idx - 1])[1].strip()
            resume_start_time = re.split('[\[\]]', loglist[idx])[1].strip()
            idx += 1
        if 'Restarting tasks' in loglist[idx]:
            resume_end_time = re.split('[\[\]]', loglist[idx])[1].strip()
        if end_marker in loglist[idx]:
            run = 'PASS'
            break

    sleep_elapsed = float(sleep_end_time) - float(sleep_start_time)
    resume_elapsed = float(resume_end_time) - float(resume_start_time)
    return (run, sleep_elapsed, resume_elapsed)


def average_times(runs):
    sleep_total = 0.0
    resume_total = 0.0
    run_count = 0
    for run in runs.keys():
        run_count += 1
        sleep_total += runs[run][1]
        resume_total += runs[run][2]
    sleep_avg = sleep_total / run_count
    resume_avg = resume_total / run_count
    print('Average time to sleep: %0.5f' % sleep_avg)
    print('Average time to resume: %0.5f' % resume_avg)


def fix_sleep_args(args):
    new_args = []
    for arg in args:
        if "=" in arg:
            new_args.extend(arg.split('='))
        else:
            new_args.append(arg)
    return new_args


def detect_progress_indicator():
    # Return a command suitable for piping progress information to its
    # stdin (invoked via Popen), in list format.
    # Return zenity if installed and DISPLAY (--auto-close)
    # return dialog if installed and no DISPLAY (width height)
    display = os.environ.get('DISPLAY')
    if display and which('zenity'):
        return ["zenity", "--progress", "--text", "Progress", "--auto-close"]
    if not display and which('dialog'):
        return ["dialog", "--gauge", "Progress", "20", "70"]
    # Return empty list if no progress indicator is to be used
    return []


def main():
    description_text = 'Tests the system BIOS using the Firmware Test Suite'
    epilog_text = ('To perform sleep testing, you will need at least some of '
                   'the following options: \n'
                   's3 or s4: tells fwts which type of sleep to perform.\n'
                   '--s3-delay-delta\n'
                   '--s3-device-check\n'
                   '--s3-device-check-delay\n'
                   '--s3-hybrid-sleep\n'
                   '--s3-max-delay\n'
                   '--s3-min-delay\n'
                   '--s3-multiple\n'
                   '--s3-quirks\n'
                   '--s3-sleep-delay\n'
                   '--s3power-sleep-delay\n\n'
                   'Example: fwts_test --sleep s3 --s3-min-delay 30 '
                   '--s3-multiple 10 --s3-device-check\n\n'
                   'For further help with sleep options:\n'
                   'fwts_test --fwts-help')
    parser = ArgumentParser(description=description_text,
                            epilog=epilog_text,
                            formatter_class=RawTextHelpFormatter)
    parser.add_argument('-l', '--log',
                        default='/tmp/fwts_results.log',
                        help=('Specify the location and name '
                              'of the log file.\n'
                              '[Default: %(default)s]'))
    parser.add_argument('-f', '--fail-level',
                        default='critical',
                        choices=['critical', 'high', 'medium',
                                 'low', 'none', 'aborted'],
                        help=('Specify the FWTS failure level that will '
                              'trigger this script to return a failing exit '
                              'code. For example, if you chose "critical" as '
                              'the fail-level, this wrapper will NOT return '
                              'a failing exit code unless FWTS reports a '
                              'test as FAILED_CRITICAL. You will still be '
                              'notified of all FWTS test failures. '
                              '[Default level: %(default)s]'))
    sleep_args = parser.add_argument_group('Sleep Options',
                                           ('The following arguments are to '
                                            'only be used with the '
                                            '--sleep test option'))
    sleep_args.add_argument('--sleep-time',
                            dest='sleep_time',
                            action='store',
                            help=('The max time in seconds that a system '
                                  'should take\nto completely enter sleep. '
                                  'Anything more than this\ntime will cause '
                                  'that test iteration to fail.\n'
                                  '[Default: 10s]'))
    sleep_args.add_argument('--resume-time',
                            dest='resume_time',
                            action='store',
                            help=('Same as --sleep-time, except this applies '
                                  'to the\ntime it takes a system to fully '
                                  'wake from sleep.\n[Default: 3s]'))

    group = parser.add_mutually_exclusive_group()
    group.add_argument('-t', '--test',
                       action='append',
                       help='Name of the test to run.')
    group.add_argument('-a', '--all',
                       action='store_true',
                       help='Run ALL FWTS automated tests (assumes -w and -c)')
    group.add_argument('-s', '--sleep',
                       nargs=REMAINDER,
                       action='store',
                       help=('Perform sleep test(s) using the additional\n'
                             'arguments provided after --sleep. Remaining\n'
                             'items on the command line will be passed \n'
                             'through to fwts for performing sleep tests. \n'
                             'For info on these extra fwts options, please \n'
                             'see the epilog below and \n'
                             'the --fwts-help option.'))
    group.add_argument('--hwe',
                       action='store_true',
                       help='Run HWE concerned tests in fwts')
    group.add_argument('--qa',
                       action='store_true',
                       help='Run QA concerned tests in fwts')
    group.add_argument('--fwts-help',
                       dest='fwts_help',
                       action='store_true',
                       help='Display the help info for fwts itself (lengthy)')
    group.add_argument('--list',
                       action='store_true',
                       help='List all tests in fwts.')
    group.add_argument('--list-hwe',
                       action='store_true',
                       help='List all HWE concerned tests in fwts')
    group.add_argument('--list-qa',
                       action='store_true',
                       help='List all QA concerned tests in fwts')
    args = parser.parse_args()

    tests = []
    results = {}
    critical_fails = []
    high_fails = []
    medium_fails = []
    low_fails = []
    passed = []
    aborted = []

    # Set correct fail level
    if args.fail_level is not 'none':
        args.fail_level = 'FAILED_%s' % args.fail_level.upper()

        # Get our failure priority and create the priority values
        fail_levels = {'FAILED_CRITICAL': 4,
                       'FAILED_HIGH': 3,
                       'FAILED_MEDIUM': 2,
                       'FAILED_LOW': 1,
                       'FAILED_NONE': 0,
                       'FAILED_ABORTED': -1}
        fail_priority = fail_levels[args.fail_level]

    # Enforce only using sleep opts with --sleep
    if args.sleep_time or args.resume_time and not args.sleep:
        parser.error('--sleep-time and --resume-time only apply to the '
                     '--sleep testing option.')
    if args.fwts_help:
        Popen('fwts -h', shell=True).communicate()[0]
        return 0
    elif args.list:
        print('\n'.join(TESTS))
        return 0
    elif args.list_hwe:
        print('\n'.join(HWE_TESTS))
        return 0
    elif args.list_qa:
        print('\n'.join(QA_TESTS))
        return 0
    elif args.test:
        tests.extend(args.test)
    elif args.hwe:
        tests.extend(HWE_TESTS)
    elif args.qa:
        tests.extend(QA_TESTS)
    elif args.sleep:
        args.sleep = fix_sleep_args(args.sleep)
        iterations = 1
        # if multiple iterations are requested, we need to intercept
        # that argument and keep it from being presented to fwts since
        # we're handling the iterations directly.
        s3 = '--s3-multiple'
        s4 = '--s4-multiple'
        if s3 in args.sleep:
            iterations = int(args.sleep.pop(args.sleep.index(s3) + 1))
            args.sleep.remove(s3)
        if s4 in args.sleep:
            iterations = int(args.sleep.pop(args.sleep.index(s4) + 1))
            args.sleep.remove(s4)
        # if we've passed our custom sleep arguments for resume or sleep
        # time, we need to intercept those as well.
        resume_time_arg = '--resume-time'
        sleep_time_arg = '--sleep-time'
        if resume_time_arg in args.sleep:
            args.resume_time = int(args.sleep.pop(
                                   args.sleep.index(resume_time_arg) + 1))
            args.sleep.remove(resume_time_arg)
        if sleep_time_arg in args.sleep:
            args.sleep_time = int(args.sleep.pop(
                                  args.sleep.index(sleep_time_arg) + 1))
            args.sleep.remove(sleep_time_arg)
        # if we still haven't set a sleep or resume time, use defauts.
        if not args.sleep_time:
            args.sleep_time = 10
        if not args.resume_time:
            args.resume_time = 3
        tests.extend(args.sleep)
    else:
        tests.extend(TESTS)

    # run the tests we want
    if args.sleep:
        iteration_results = {}
        print('=' * 20 + ' Test Results ' + '=' * 20)
        progress_indicator = None
        if detect_progress_indicator():
            progress_indicator = Popen(detect_progress_indicator(),
                                       stdin=PIPE)
        for iteration in range(0, iterations):
            timestamp = int(time())
            start_marker = 'CHECKBOX SLEEP TEST START %s' % timestamp
            end_marker = 'CHECKBOX SLEEP TEST STOP %s' % timestamp
            syslog(LOG_INFO, '---' + start_marker + '---' + str(time()))
            command = ('fwts -q --stdout-summary -r %s %s'
                       % (args.log, ' '.join(tests)))
            results['sleep'] = (Popen(command, stdout=PIPE, shell=True)
                                .communicate()[0].strip()).decode()
            syslog(LOG_INFO, '---' + end_marker + '---' + str(time()))
            if 's4' not in args.sleep:
                sleep_times = get_sleep_times(start_marker,
                                              end_marker,
                                              args.sleep_time,
                                              args.resume_time)
                iteration_results[iteration] = sleep_times
                progress_tuple = (iteration,
                                  iteration_results[iteration][0],
                                  iteration_results[iteration][1],
                                  iteration_results[iteration][2])
                progress_string = (' - Cycle %s: Status: %s  '
                                   'Sleep Elapsed: %0.5f    '
                                   'Resume Elapsed: '
                                   ' %0.5f' % progress_tuple)
                progress_pct = "{}".format(int(100 * iteration / iterations))
                if "zenity" in detect_progress_indicator():
                    progress_indicator.stdin.write("# {}\n".format(
                        progress_string).encode('utf-8'))
                    progress_indicator.stdin.write("{}\n".format(
                        progress_pct).encode('utf-8'))
                    progress_indicator.stdin.flush()
                elif "dialog" in detect_progress_indicator():
                    progress_indicator.stdin.write("XXX\n".encode('utf-8'))
                    progress_indicator.stdin.write(
                        progress_pct.encode('utf-8'))
                    progress_indicator.stdin.write(
                        "\nTest progress\n".encode('utf-8'))
                    progress_indicator.stdin.write(
                        progress_string.encode('utf-8'))
                    progress_indicator.stdin.write(
                        "\nXXX\n".encode('utf-8'))
                    progress_indicator.stdin.flush()
                else:
                    print(progress_string)
        if detect_progress_indicator():
            progress_indicator.terminate()

        if 's4' not in args.sleep:
            average_times(iteration_results)
            for run in iteration_results.keys():
                if 'FAIL' in iteration_results[run]:
                    results['sleep'] = 'FAILED_CRITICAL'
    else:
        for test in tests:
            # ACPI tests can now be run with --acpitests (fwts >= 15.07.00)
            log = args.log
            # Split the log file for HWE (only if -t is not used)
            if test == 'acpitests':
                test = '--acpitests'
            command = ('fwts -q --stdout-summary -r %s %s'
                       % (log, test))
            results[test] = (Popen(command, stdout=PIPE, shell=True)
                             .communicate()[0].strip()).decode()

    # parse the summaries
    for test in results.keys():
        if 'FAILED_CRITICAL' in results[test]:
            critical_fails.append(test)
        if 'FAILED_HIGH' in results[test]:
            high_fails.append(test)
        if 'FAILED_MEDIUM' in results[test]:
            medium_fails.append(test)
        if 'FAILED_LOW' in results[test]:
            low_fails.append(test)
        if 'PASSED' in results[test]:
            passed.append(test)
        if 'ABORTED' in results[test]:
            aborted.append(test)
        else:
            continue

    if critical_fails:
        print("Critical Failures: %d" % len(critical_fails))
        print("WARNING: The following test cases were reported as critical\n"
              "level failures by fwts. Please review the log at\n"
              "%s for more information." % args.log)
        for test in critical_fails:
            print(" - " + test)
    if high_fails:
        print("High Failures: %d" % len(high_fails))
        print("WARNING: The following test cases were reported as high\n"
              "level failures by fwts. Please review the log at\n"
              "%s for more information." % args.log)
        for test in high_fails:
            print(" - " + test)
    if medium_fails:
        print("Medium Failures: %d" % len(medium_fails))
        print("WARNING: The following test cases were reported as medium\n"
              "level failures by fwts. Please review the log at\n"
              "%s for more information." % args.log)
        for test in medium_fails:
            print(" - " + test)
    if low_fails:
        print("Low Failures: %d" % len(low_fails))
        print("WARNING: The following test cases were reported as low\n"
              "level failures by fwts. Please review the log at\n"
              "%s for more information." % args.log)
        for test in low_fails:
            print(" - " + test)
    if passed:
        print("Passed: %d" % len(passed))
        for test in passed:
            print(" - " + test)
    if aborted:
        print("Aborted Tests: %d" % len(aborted))
        print("WARNING: The following test cases were aborted by fwts\n"
              "Please review the log at %s for more information."
              % args.log)
        for test in aborted:
            print(" - " + test)

    if args.fail_level is not 'none':
        if fail_priority == fail_levels['FAILED_CRITICAL']:
            if critical_fails:
                return 1
        if fail_priority == fail_levels['FAILED_HIGH']:
            if critical_fails or high_fails:
                return 1
        if fail_priority == fail_levels['FAILED_MEDIUM']:
            if critical_fails or high_fails or medium_fails:
                return 1
        if fail_priority == fail_levels['FAILED_LOW']:
            if critical_fails or high_fails or medium_fails or low_fails:
                return 1
        if fail_priority == fail_levels['FAILED_ABORTED']:
            if aborted or critical_fails or high_fails:
                return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())
