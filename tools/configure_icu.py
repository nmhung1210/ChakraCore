#!/usr/bin/env python
#-------------------------------------------------------------------------------------------------------
# Copyright (C) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.txt file in the project root for full license information.
#-------------------------------------------------------------------------------------------------------

from __future__ import print_function
import hashlib
import os
import shutil
import sys
import tarfile
import urllib
import urllib2
from zipfile import ZipFile
from argparse import ArgumentParser

# Parse Makefile.in to find the OBJECTS = ... list of object files
# This is the officially recommended way of integrating ICU into a large project's build system
def get_sources(icuroot, mkin_path):
    # ignore these files, similar to Node
    ignore = [
        "source/tools/toolutil/udbgutil.cpp",
        "source/tools/toolutil/dbgutil.cpp",
    ]
    ignore = [os.path.join(icuroot, os.path.normpath(source)) for source in ignore]

    def get_source(object_path):
        base = os.path.splitext(object_path)[0]
        cpp = base + ".cpp"
        c = base + ".c"

        # return None if we find a source but explicitly exclude it, compared
        # to raising an exception if a source is referenced that doesn't exist,
        # since that is more likely to be an issue with the source/ folder
        if cpp in ignore or c in ignore:
            return None

        if os.path.isfile(cpp):
            return cpp
        elif os.path.isfile(c):
            return c

        raise Exception("%s has no corresponding source file" % object_path)

    with open(mkin_path, "r") as mkin_contents:
        in_objs = False
        sources = []

        for line in mkin_contents:
            line = line.strip()

            if line[:7] == "OBJECTS":
                in_objs = True
                # trim " = " in "OBJECTS = {object files}"
                line = line[10:]

            elif in_objs == True and len(line) == 0:
                # done with OBJECTS, return
                return sources

            if in_objs == True:
                # trim " \" in "{object files} \"
                linelen = len(line)
                line = line[:linelen - 1].strip() if line[linelen - 1] == "\\" else line

                objects = map(lambda o: os.path.join(os.path.dirname(mkin_path), o), line.split())
                cpps = map(get_source, objects)
                cpps = filter(lambda cpp: cpp != None, cpps)
                sources.extend(cpps)

        # should have returned by now
        raise Exception("Could not extract sources from %s" % mkin_path)

def get_headers(icuroot, headers_path):
    # ignore these files, similar to Node
    ignore = [
        "source/tools/toolutil/udbgutil.h",
        "source/tools/toolutil/dbgutil.h",
    ]
    ignore = [os.path.join(icuroot, os.path.normpath(source)) for source in ignore]

    if not os.path.isdir(headers_path):
        raise Exception("%s is not a valid headers path" % headers_path)

    headers = map(lambda h: os.path.join(headers_path, h), os.listdir(headers_path))
    headers = filter(lambda h: os.path.splitext(h)[1] == ".h", headers) # only include .h files
    headers = filter(lambda h: h not in ignore, headers) # don't include ignored headers
    return headers

def create_msvc_props(chakra_icu_root, icu_sources_root, version):
    prelude = """<?xml version="1.0" encoding="utf-8"?>
<!-- DO NOT EDIT THIS FILE. It is auto-generated by $ChakraCore/tools/%s -->
<Project DefaultTargets="Build" ToolsVersion="12.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>""" % os.path.basename(__file__)

    prop_template = """
    <Icu{0}{1}>
      {2}
    </Icu{0}{1}>"""

    conclusion = """
  </PropertyGroup>
</Project>
"""
    joiner = ";\n      "

    def add_props(propfile, deproot, dep):
        sources = get_sources(icu_sources_root, os.path.join(deproot, dep, "Makefile.in"))
        sources_prop = prop_template.format(dep.capitalize(), "Sources", joiner.join(sources))
        propfile.write(sources_prop)

        headers = get_headers(icu_sources_root, os.path.join(deproot, dep))
        headers_prop = prop_template.format(dep.capitalize(), "Headers", joiner.join(headers))
        propfile.write(headers_prop)

    with open(os.path.join(chakra_icu_root, "Chakra.ICU.props"), "w") as propfile:
        propfile.write(prelude)

        # Write all of the sources and header files to Icu${Dep}${DepKind}, such as IcuCommonSources
        sourceroot = os.path.join(icu_sources_root, "source")
        for dep in ["common", "i18n", "stubdata"]:
            add_props(propfile, sourceroot, dep)

        # tools are handled somewhat differently, since theyre in source/tools
        toolsroot = os.path.join(sourceroot, "tools")
        for dep in ["toolutil", "genccode"]:
            add_props(propfile, toolsroot, dep)

        version_parts = version.split(".")
        no_newline_prop_template = "\n    <Icu{0}>{1}</Icu{0}>"
        propfile.write(no_newline_prop_template.format("VersionMajor", version_parts[0]))
        propfile.write(no_newline_prop_template.format("VersionMinor", version_parts[1] if len(version_parts) > 1 else "0"))

        propfile.write(no_newline_prop_template.format("SourceDirectory", sourceroot))

        include_dirs = [os.path.join(sourceroot, component) for component in ["common", "i18n"]]
        propfile.write(prop_template.format("Include", "Directories", joiner.join(include_dirs)))

        propfile.write(conclusion)

def download_icu(icuroot, version, yes):
    # download either the zip or tar, depending on the os
    extension = "zip" if os.name == "nt" else "tgz"

    archive_file = "icu4c-{0}-src.{1}".format(version.replace(".", "_"), extension)
    md5_file = "icu4c-src-{0}.md5".format(version.replace(".", "_"))

    archive_url = "http://download.icu-project.org/files/icu4c/{0}/{1}".format(version, archive_file)
    md5_url = "https://ssl.icu-project.org/files/icu4c/{0}/{1}".format(version, md5_file)

    license_confirmation = """
{1}
This script downloads ICU from {0}.
It is licensed to you by its publisher, not Microsoft.
Microsoft is not responsible for the software.
Your installation and use of ICU is subject to the publisher's terms,
which are available here: http://www.unicode.org/copyright.html#License
{1}
""".format(archive_url, "-" * 80)
    if not yes:
        print(license_confirmation)
        response = raw_input("Do you agree to these terms? [Y/n] ")
        if response != "" and response != "y" and response != "Y":
            sys.exit(0)

    print("Downloading ICU from %s" % archive_url)

    archive_path = urllib.urlretrieve(archive_url)[0]

    print("Downloaded ICU to %s" % archive_path)

    # check the hash of the download zipfile/tarball
    checksum = ""
    with open(archive_path, "rb") as download:
        md5 = hashlib.md5()
        md5.update(download.read())
        checksum = md5.hexdigest()

    md5_path = os.path.join(icuroot, md5_file)
    md5_request = urllib2.urlopen(md5_url)
    md5s = md5_request.read().decode("ascii").split("\n")
    relevant_md5 = filter(lambda line: line[len(line) - len(archive_file):] == archive_file, md5s)
    if len(relevant_md5) != 1:
        raise Exception("Could not find md5 hash for %s in %s" % archive_file, md5_url)

    correct_hash = relevant_md5[0]
    correct_hash = correct_hash.split(" ")[0]
    if (correct_hash == checksum):
        print("MD5 checksums match, continuing")
        return archive_path
    else:
        raise Exception("MD5 checksums do not match. Expected %s, got %s" % correct_hash, checksum)

def extract_icu(icuroot, archive_path):
    tempdir = os.path.normpath(os.path.join(icuroot, "temp"))
    print("Extracting ICU to %s" % tempdir)
    opener = ZipFile if os.name == "nt" else tarfile.open
    with opener(archive_path, "r") as archive:
        archive.extractall(tempdir)

    icu_folder = os.path.join(icuroot, "icu")
    if os.path.isdir(icu_folder):
        shutil.rmtree(icu_folder)

    print("Extraction successful, ICU will be located at %s" % icu_folder)
    shutil.move(os.path.join(tempdir, "icu"), icu_folder)
    shutil.rmtree(tempdir)

def main():
    chakra_icu_root = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "deps", "Chakra.ICU"))

    argparser = ArgumentParser(description = "Download and set up ICU for use in ChakraCore")
    argparser.add_argument("-y", "--yes", action = "store_true", help = "Skip ICU License prompt text")
    argparser.add_argument("version", help = "ICU version to download. Not compatible with --archive", default = "60.2", nargs = "?")
    argparser.add_argument("-i", "--icu-root",
        help = "Path to directory to extract ICU to. Resulting directory will contain a single subfolder, 'icu', which contains ICU's source tree",
        default = chakra_icu_root
    )
    argparser.add_argument("-a", "--archive", help = "Path to icu.zip (Windows) or icu.tar (POSIX) that you have already downloaded")
    args = argparser.parse_args()

    archive_path = args.archive
    if args.version is not None and args.archive is None:
        archive_path = download_icu(args.icu_root, args.version, args.yes)

    extract_icu(args.icu_root, archive_path)
    if os.name == "nt":
        create_msvc_props(chakra_icu_root, os.path.join(args.icu_root, "icu"), args.version)

if __name__ == "__main__":
    main()
