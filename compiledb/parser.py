#!/usr/bin/env python
#
#   compiledb-generator: Tool for generating LLVM Compilation Database
#   files for make-based build systems.
#
#   Copyright (c) 2017 Nick Diego Yamane <nick.diego@gmail.com>
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
import bashlex.parser
import bashlex.ast
import re
import subprocess
from sys import version_info


# Internal variables used to parse build log entries
# TODO: Most of them will be removed soon in favor of an
# bashlex/argparse based parsing (mainly for command line options
# in to avoid reinventing the wheel and eliminate false positives
# for compiler, wrappers and their flags
cc_compile_regex = re.compile("(.*-?g?cc )|(.*-?clang )")
cpp_compile_regex = re.compile("(.*-?[gc]\+\+ )|(.*-?clang\+\+ )")
file_regex = re.compile("(^.+\.c$)|(^.+\.cc$)|(^.+\.cpp$)|(^.+\.cxx$)")

# Leverage make --print-directory option
make_enter_dir = re.compile("^\s*make\[\d+\]: Entering directory [`\'\"](?P<dir>.*)[`\'\"]\s*$")
make_leave_dir = re.compile("^\s*make\[\d+\]: Leaving directory .*$")


class ParsingResult(object):
    def __init__(self):
        self.skipped = 0
        self.count = 0
        self.compdb = []

    def __str__(self):
        return "Line count: {}, Skipped: {}, Entries: {}".format(
            self.count, self.skipped, str(self.compdb))


class Error(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return "Error: {}".format(self.msg)


class NodeVisitor(bashlex.ast.nodevisitor):
    def __init__(self, substitutions):
        self.substitutions = substitutions

    def visitcommandsubstitution(self, n, command):
        self.substitutions.append(n)
        # do not recurse into child nodes
        return False


def parse_build_log(build_log, proj_dir, inc_prefix, exclude_list, verbose):
    result = ParsingResult()

    exclude_regex = None
    if len(exclude_list) > 0:
        try:
            exclude_regex = re.compile("|".join(exclude_list))
        except:
            raise Error('Regular expression not valid: {}'.format(exclude_list))

    dir_stack = [proj_dir]
    working_dir = proj_dir
    lineno = 0

    # Process build log
    for line in build_log:
        lineno += 1
        # Concatenate line if need
        accumulate_line = line
        while (line.endswith('\\\n')):
            accumulate_line = accumulate_line[:-2]
            line = next(build_log, '')
            accumulate_line += line
        line = accumulate_line.rstrip()

        # Parse directory that make entering/leaving
        enter_dir = make_enter_dir.match(line)
        if (make_enter_dir.match(line)):
            working_dir = enter_dir.group('dir')
            dir_stack.append(working_dir)
            continue
        if (make_leave_dir.match(line)):
            dir_stack.pop()
            working_dir = dir_stack[-1]
            continue

        # Check if it looks like an entry of interest and
        # and try to determine the compiler
        # TODO: Refactor to use bashlex + argparse/optparse
        if not cc_compile_regex.match(line) and not cpp_compile_regex.match(line):
            result.skipped += 1
            continue

        try:
            # Uses bashlex to parse and process sh/bash
            # substitution commands
            line = preprocess_cmd(line, working_dir)
        except Exception as err:
            if verbose:
                print(('[INFO] Line {}: Failed to parse build command '
                       '[Details: {}]').format(lineno, str(err)))
            result.skipped += 1
            continue

        words = split_cmd_line(line)
        filepath = None

        for word in words:
            if (file_regex.match(word)):
                filepath = word

        if filepath and exclude_regex and exclude_regex.match(filepath):
            if verbose:
                print('[INFO] Line {}: Excluding file {}'.format(lineno, filepath))
            result.skipped += 1
            continue

        if filepath is None:
            if verbose:
                print("[INFO] Line {}: Empty file name. Ignoring: {}".format(lineno, line.strip()))
            result.skipped += 1
            continue
        else:
            result.count += 1

        # add entry to database
        # TODO performance: serialize to json file here?
        if (verbose):
            print("args={} --> {}".format(len(line), filepath))

        result.compdb.append({
            'directory': working_dir,
            'command': line,
            'file': filepath,
        })

    return result


def preprocess_cmd(line, working_dir):
    """Uses bashlex to parse and process sh/bash substitution commands.
    May result in a parsing exception for invalid commands."""

    trees = bashlex.parser.parse(line)
    subst_nodes = []
    for tree in trees:
        visitor = NodeVisitor(subst_nodes)
        visitor.visit(tree)

    # do replacements from the end so the indicies will be correct
    subst_nodes.reverse()
    postprocessed = list(line)

    for node in subst_nodes:
        start, end = node.command.pos
        subst_cmd = line[start:end]

        start, end = node.pos
        out = run_cmd(subst_cmd, shell=True, cwd=working_dir)
        postprocessed[start:end] = out.strip()

    return ''.join(postprocessed)


def split_cmd_line(line):
    # Pass 1: split line using whitespace
    words = line.strip().split()
    # Pass 2: merge words so that the no. of quotes is balanced
    res = []
    for w in words:
        if(len(res) > 0 and unbalanced_quotes(res[-1])):
            res[-1] += " " + w
        else:
            res.append(w)
    return res


def unbalanced_quotes(s):
    single = 0
    double = 0
    for c in s:
        if(c == "'"):
            single += 1
        elif(c == '"'):
            double += 1
    return (single % 2 == 1 or double % 2 == 1)


if version_info[0] >= 3:  # Python 3
    def run_cmd(cmd, encoding='utf-8', **kwargs):
        return subprocess.check_output(cmd, encoding=encoding, **kwargs)
else:  # Python 2
    def run_cmd(cmd, encoding='utf-8', **kwargs):
        return subprocess.check_output(cmd, **kwargs)

# ex: ts=2 sw=4 et filetype=python
