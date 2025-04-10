#! /usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (c) 2009-2015, 2017-18, The Linux Foundation. All rights reserved.

# Build the kernel for all targets using the Android build environment.

from collections import namedtuple
import glob
from optparse import OptionParser
import os
import re
import shutil
import subprocess
import sys
import threading
import queue

version = 'build-all.py, version 1.99'

build_dir = '../all-kernels'
make_command = ["vmlinux", "modules", "dtbs"]
all_options = {}
compile64 = os.environ.get('CROSS_COMPILE64')
clang_bin = os.environ.get('CLANG_BIN')

def error(msg):
    sys.stderr.write("error: %s\n" % msg)

def fail(msg):
    """Fail with a user-printed message"""
    error(msg)
    sys.exit(1)

if not os.environ.get('CROSS_COMPILE'):
    fail("CROSS_COMPILE must be set in the environment")

def check_kernel():
    """Ensure that PWD is a kernel directory"""
    if not os.path.isfile('MAINTAINERS'):
        fail("This doesn't seem to be a kernel dir")

def check_build():
    """Ensure that the build directory is present."""
    if not os.path.isdir(build_dir):
        try:
            os.makedirs(build_dir)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                pass
            else:
                raise

failed_targets = []

BuildResult = namedtuple('BuildResult', ['status', 'messages'])

class BuildSequence(namedtuple('BuildSequence', ['log_name', 'short_name', 'steps'])):

    def set_width(self, width):
        self.width = width

    def __enter__(self):
        self.log = open(self.log_name, 'w')
    def __exit__(self, type, value, traceback):
        self.log.close()

    def run(self):
        self.status = None
        messages = ["Building: " + self.short_name]
        def printer(line):
            text = "[%-*s] %s" % (self.width, self.short_name, line)
            messages.append(text)
            self.log.write(text)
            self.log.write('\n')
        for step in self.steps:
            st = step.run(printer)
            if st:
                self.status = BuildResult(self.short_name, messages)
                break
        if not self.status:
            self.status = BuildResult(None, messages)

class BuildTracker:
    """Manages all of the steps necessary to perform a build.  The
    build consists of one or more sequences of steps.  The different
    sequences can be processed independently, while the steps within a
    sequence must be done in order."""

    def __init__(self, parallel_builds):
        self.sequence = []
        self.lock = threading.Lock()
        self.parallel_builds = parallel_builds

    def add_sequence(self, log_name, short_name, steps):
        self.sequence.append(BuildSequence(log_name, short_name, steps))

    def longest_name(self):
        longest = 0
        for seq in self.sequence:
            longest = max(longest, len(seq.short_name))
        return longest

    def __repr__(self):
        return "BuildTracker(%s)" % self.sequence

    def run_child(self, seq):
        seq.set_width(self.longest)
        tok = self.build_tokens.get()
        with self.lock:
            print("Building:", seq.short_name)
        with seq:
            seq.run()
            self.results.put(seq.status)
        self.build_tokens.put(tok)

    def run(self):
        self.longest = self.longest_name()
        self.results = Queue.Queue()
        children = []
        errors = []
        self.build_tokens = Queue.Queue()
        nthreads = self.parallel_builds
        print("Building with", nthreads, "threads")
        for i in range(nthreads):
            self.build_tokens.put(True)
        for seq in self.sequence:
            child = threading.Thread(target=self.run_child, args=[seq])
            children.append(child)
            child.start()
        for child in children:
            stats = self.results.get()
            if all_options.verbose:
                with self.lock:
                    for line in stats.messages:
                        print(line)
                    sys.stdout.flush()
            if stats.status:
                errors.append(stats.status)
        for child in children:
            child.join()
        if errors:
            fail("\n  ".join(["Failed targets:"] + errors))

class PrintStep:
    """A step that just prints a message"""
    def __init__(self, message):
        self.message = message

    def run(self, outp):
        outp(self.message)

class MkdirStep:
    """A step that makes a directory"""
    def __init__(self, direc):
        self.direc = direc

    def run(self, outp):
        outp("mkdir %s" % self.direc)
        os.mkdir(self.direc)

class RmtreeStep:
    def __init__(self, direc):
        self.direc = direc

    def run(self, outp):
        outp("rmtree %s" % self.direc)
        shutil.rmtree(self.direc, ignore_errors=True)

class CopyfileStep:
    def __init__(self, src, dest):
        self.src = src
        self.dest = dest

    def run(self, outp):
        outp("cp %s %s" % (self.src, self.dest))
        shutil.copyfile(self.src, self.dest)

class ExecStep:
    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs

    def run(self, outp):
        outp("exec: %s" % (" ".join(self.cmd),))
        with open('/dev/null', 'r') as devnull:
            proc = subprocess.Popen(self.cmd, stdin=devnull,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    **self.kwargs)
            stdout = proc.stdout
            while True:
                line = stdout.readline()
                if not line:
                    break
                line = line.rstrip('\n')
                outp(line)
            result = proc.wait()
            if result != 0:
                return ('error', result)
            else:
                return None

class Builder():

    def __init__(self, name, defconfig):
        self.name = name
        self.defconfig = defconfig

        self.confname = re.sub('arch/arm[64]*/configs/', '', self.defconfig)

        # Determine if this is a 64-bit target based on the location
        # of the defconfig.
        self.make_env = os.environ.copy()
        if "/arm64/" in defconfig:
            if compile64:
                self.make_env['CROSS_COMPILE'] = compile64
            else:
                fail("Attempting to build 64-bit, without setting CROSS_COMPILE64")
            self.make_env['ARCH'] = 'arm64'
        else:
            self.make_env['ARCH'] = 'arm'
        self.make_env['KCONFIG_NOTIMESTAMP'] = 'true'
        self.log_name = "%s/log-%s.log" % (build_dir, self.name)

    def build(self):
        steps = []
        dest_dir = os.path.join(build_dir, self.name)
        log_name = "%s/log-%s.log" % (build_dir, self.name)
        steps.append(PrintStep('Building %s in %s log %s' %
            (self.name, dest_dir, log_name)))
        if not os.path.isdir(dest_dir):
            steps.append(MkdirStep(dest_dir))
        defconfig = self.defconfig
        dotconfig = '%s/.config' % dest_dir
        savedefconfig = '%s/defconfig' % dest_dir

        staging_dir = 'install_staging'
        modi_dir = '%s' % staging_dir
        hdri_dir = '%s/usr' % staging_dir
        steps.append(RmtreeStep(os.path.join(dest_dir, staging_dir)))

        steps.append(ExecStep(['make', 'O=%s' % dest_dir,
            self.confname], env=self.make_env))

        # Build targets can be dependent upon the completion of
        # previous build targets, so build them one at a time.
        cmd_line = ['make',
            'INSTALL_HDR_PATH=%s' % hdri_dir,
            'INSTALL_MOD_PATH=%s' % modi_dir,
            'O=%s' % dest_dir,
            'REAL_CC=%s' % clang_bin]
        build_targets = []
        for c in make_command:
            if re.match(r'^-{1,2}\w', c):
                cmd_line.append(c)
            else:
                build_targets.append(c)
        for t in build_targets:
            steps.append(ExecStep(cmd_line + [t], env=self.make_env))

        return steps

def scan_configs():
    """Get the full list of defconfigs appropriate for this tree."""
    names = []
    for defconfig in glob.glob('arch/arm*/configs/vendor/*_defconfig'):
        target = os.path.basename(defconfig)[:-10]
        name = target + "-llvm"
        if 'arch/arm64' in defconfig:
            name = name + "-64"
        names.append(Builder(name, defconfig))

    return names

def build_many(targets):
    print("Building %d target(s)" % len(targets))

    # To try and make up for the link phase being serial, try to do
    # two full builds in parallel.  Don't do too many because lots of
    # parallel builds tends to use up available memory rather quickly.
    parallel = 2
    if all_options.jobs and all_options.jobs > 1:
        j = max(all_options.jobs / parallel, 2)
        make_command.append("-j" + str(j))

    tracker = BuildTracker(parallel)
    for target in targets:
        steps = target.build()
        tracker.add_sequence(target.log_name, target.name, steps)
    tracker.run()

def main():
    global make_command

    check_kernel()
    check_build()

    configs = scan_configs()

    usage = ("""
           %prog [options] all                 -- Build all targets
           %prog [options] target target ...   -- List specific targets
           """)
    parser = OptionParser(usage=usage, version=version)
    parser.add_option('--list', action='store_true',
            dest='list',
            help='List available targets')
    parser.add_option('-v', '--verbose', action='store_true',
            dest='verbose',
            help='Output to stdout in addition to log file')
    parser.add_option('-j', '--jobs', type='int', dest="jobs",
            help="Number of simultaneous jobs")
    parser.add_option('-l', '--load-average', type='int',
            dest='load_average',
            help="Don't start multiple jobs unless load is below LOAD_AVERAGE")
    parser.add_option('-k', '--keep-going', action='store_true',
            dest='keep_going', default=False,
            help="Keep building other targets if a target fails")
    parser.add_option('-m', '--make-target', action='append',
            help='Build the indicated make target (default: %s)' %
                 ' '.join(make_command))

    (options, args) = parser.parse_args()
    global all_options
    all_options = options

    if options.list:
        print("Available targets:")
        for target in configs:
            print("   %s" % target.name)
        sys.exit(0)

    if options.make_target:
        make_command = options.make_target

    if args == ['all']:
        build_many(configs)
    elif len(args) > 0:
        all_configs = {}
        for t in configs:
            all_configs[t.name] = t
        targets = []
        for t in args:
            if t not in all_configs:
                parser.error("Target '%s' not one of %s" % (t, all_configs.keys()))
            targets.append(all_configs[t])
        build_many(targets)
    else:
        parser.error("Must specify a target to build, or 'all'")

if __name__ == "__main__":
    main()
