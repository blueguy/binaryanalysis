#!/usr/bin/python

## Binary Analysis Tool
## Copyright 2013 Armijn Hemel for Tjaldur Software Governance Solutions
## Licensed under Apache 2.0, see LICENSE file for details

import os, os.path, sys, subprocess, copy, cPickle, tempfile, hashlib, shutil, multiprocessing, piecharts

'''
This plugin is used to generate pictures. It is run as an aggregate scan for
a reason: as it turns out many pictures that are generated are identical:
piecharts of programs from the same package are often the same, version
information is often the same since the same database is used.

This is especially true for Java class files where there are often just a few
strings or methods from a single class file, which can lead to 80% of the
pictures being exact duplicates.

Since generating pictures can have quite a bit of overhead (especially with
the current scripts) it makes sense to first deduplicate and then generate
pictures.

The method works as follows:

1. All data from pickles that is needed to generate pictures is extracted.
2. The checksum of the pickle is computed and recorded. If there is a duplicate
the pickle is removed and it is recorded which file it originally belonged to.
3. Pictures are generated in parallel for the remaining pickle files.
4. The pictures are copied and renamed.
'''

def generateversionchart((versionpickle, picklehash, imagedir, pickledir)):
	p = subprocess.Popen(['bat-generate-version-chart.py', '-i', os.path.join(pickledir, versionpickle), '-o', '%s/%s.png' % (imagedir, picklehash)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
	(stanout, stanerr) = p.communicate()
	if p.returncode != 0:
		print >>sys.stderr, stanerr
		return None
	else:
		return '%s.png' % (picklehash, )

## compute a SHA256 hash. This is done in chunks to prevent a big file from
## being read in its entirety at once, slowing down a machine.
def gethash(path):
	scanfile = open(path, 'r')
	h = hashlib.new('sha256')
	scanfile.seek(0)
	hashdata = scanfile.read(10000000)
	while hashdata != '':
		h.update(hashdata)
		hashdata = scanfile.read(10000000)
	scanfile.close()
	return h.hexdigest()

def generateimages(unpackreports, scantempdir, topleveldir, envvars=None):
	scanenv = os.environ.copy()
	if envvars != None:
		for en in envvars.split(':'):
			try:
				(envname, envvalue) = en.split('=')
				scanenv[envname] = envvalue
			except Exception, e:
				pass

	imagedir = scanenv.get('BAT_IMAGEDIR', "%s/%s" % (topleveldir, "images"))
	try:
		os.stat(imagedir)
	except:
		## BAT_IMAGEDIR does not exist
		try:
			os.makedirs(imagedir)
		except Exception, e:
			return

	pickledir = scanenv.get('BAT_PICKLEDIR', "%s/%s" % (topleveldir, "pickles"))
	try:
		os.stat(pickledir)
	except:
		## BAT_PICKLEDIR does not exist
		try:
			os.makedirs(pickledir)
		except Exception, e:
			return

	symlinks = False
	if scanenv.get('AGGREGATE_IMAGE_SYMLINK', 0) == '1':
		symlinks = True

	rankingfiles = []

	## filter out the files which don't have ranking results
	for i in unpackreports:
		if not unpackreports[i].has_key('sha256'):
			continue
		if not unpackreports[i].has_key('tags'):
			continue
		if not 'ranking' in unpackreports[i]['tags']:
			continue
		filehash = unpackreports[i]['sha256']
		if not os.path.exists(os.path.join(topleveldir, "filereports", "%s-filereport.pickle" % filehash)):
			continue
		rankingfiles.append(i)

	pickles = []
	piepickles = []
	processed = []
	funcpicklespackages = []
	versionpicklespackages = []
	piepicklespackages = []
	picklehashes = {}
	pickletofile = {}
	for r in rankingfiles:
		filehash = unpackreports[r]['sha256']
		if filehash in processed:
			continue

		leaf_file = open(os.path.join(topleveldir, "filereports", "%s-filereport.pickle" % filehash), 'rb')
		leafreports = cPickle.load(leaf_file)
		leaf_file.close()

		if leafreports.has_key('ranking'):
			## the ranking result is (res, dynamicRes, variablepvs)
			(res, dynamicRes, variablepvs) = leafreports['ranking']
			if res == None and dynamicRes == {}:
				continue

			## extract information for generating pie charts
			piedata = []
			pielabels = []
			totals = 0.0
			others = 0.0
			for j in res['reports']:
				## less than half a percent, that's not significant anymore
				if j[3] < 0.5:
					totals += j[3]
					others += j[3]
					if totals <= 99.0:
						continue
				if totals >= 99.0:
					pielabels.append("others")
					piedata.append(others + 100.0 - totals)
					break
				else:   
					pielabels.append(j[1])
					piedata.append(j[3])
					totals += j[3]

			## now dump the data to a pickle
			if pielabels != [] and piedata != []:
				tmppickle = tempfile.mkstemp()
				cPickle.dump((piedata, pielabels), os.fdopen(tmppickle[0], 'w'))
				picklehash = gethash(tmppickle[1])
				if picklehash in piepickles:
					if pickletofile.has_key(picklehash):
						pickletofile[picklehash].append(filehash)
					else:
						pickletofile[picklehash] = [filehash]
					piepicklespackages.append((picklehash, filehash))
					os.unlink(tmppickle[1])
				else:
					shutil.move(tmppickle[1], pickledir)
					piepickles.append(picklehash)
					piepicklespackages.append((picklehash, filehash))
					picklehashes[picklehash] = os.path.basename(tmppickle[1])
					if pickletofile.has_key(picklehash):
						pickletofile[picklehash].append(filehash)
					else:
						pickletofile[picklehash] = [filehash]

			## extract pickles with version information for strings for
			## generating version charts
			for j in res['reports']:
				if j[4] != {}:
					package = j[1]
					tmppickle = tempfile.mkstemp()
					pickledata = []
					vals = list(set(j[4].values()))
					vals.sort()
					for v in vals:
						j_sorted = filter(lambda x: x[1] == v, j[4].items())
						j_sorted.sort()
						for v2 in j_sorted:
							pickledata.append(v2)
					cPickle.dump(pickledata, os.fdopen(tmppickle[0], 'w'))
					picklehash = gethash(tmppickle[1])
					if picklehash in pickles:
						if pickletofile.has_key(picklehash):
							pickletofile[picklehash].append(filehash)
						else:
							pickletofile[picklehash] = [filehash]
						versionpicklespackages.append((picklehash, package))
						os.unlink(tmppickle[1])
					else:
						shutil.move(tmppickle[1], pickledir)
						pickles.append(picklehash)
						versionpicklespackages.append((picklehash, package))
						picklehashes[picklehash] = os.path.basename(tmppickle[1])
						if pickletofile.has_key(picklehash):
							pickletofile[picklehash].append(filehash)
						else:
							pickletofile[picklehash] = [filehash]

			## extract pickles with version information for functions
			if dynamicRes.has_key('packages'):
				for package in dynamicRes['packages']:
					packagedata = copy.copy(dynamicRes['packages'][package])
					tmppickle = tempfile.mkstemp()
					pickledata = []
					p_sorted = sorted(packagedata, key=lambda x: x[1])
					vals = list(set(map(lambda x: x[1], p_sorted)))
					vals.sort()
					for v in vals:
						j_sorted = filter(lambda x: x[1] == v, p_sorted)
						j_sorted.sort()
						for v2 in j_sorted:
							pickledata.append(v2)
					cPickle.dump(pickledata, os.fdopen(tmppickle[0], 'w'))
					picklehash = gethash(tmppickle[1])
					if picklehash in pickles:
						if pickletofile.has_key(picklehash):
							pickletofile[picklehash].append(filehash)
						else:
							pickletofile[picklehash] = [filehash]
						funcpicklespackages.append((picklehash, package))
						os.unlink(tmppickle[1])
					else:
						shutil.move(tmppickle[1], pickledir)
						pickles.append(picklehash)
						funcpicklespackages.append((picklehash, package))
						picklehashes[picklehash] = os.path.basename(tmppickle[1])
						if pickletofile.has_key(picklehash):
							pickletofile[picklehash].append(filehash)
						else:
							pickletofile[picklehash] = [filehash]
			processed.append(filehash)
	## create a pool and generate the images
	pool = multiprocessing.Pool()
	pietasks = []

	if piepicklespackages != []:
		pietasks = list(set(map(lambda x: (picklehashes[x[0]], pickledir, x[0], imagedir), piepicklespackages)))
		results = pool.map(piecharts.generateImages, pietasks, 1)
		for p in piepicklespackages:
			oldfilename = "%s-%s" % (p[0], "piechart.png")
			filename = "%s-%s" % (p[1], "piechart.png")
			if os.path.exists(os.path.join(imagedir, oldfilename)):
				shutil.copy(os.path.join(imagedir, oldfilename), os.path.join(imagedir, filename))
		for p in piepicklespackages:
			try:
				filename = "%s-%s" % (p[0], "piechart.png")
				os.unlink(os.path.join(imagedir, filename))
			except Exception, e:
				#print >>sys.stderr, "ERR", e
				pass

	funcpicklespackages = list(set(funcpicklespackages))
	versionpicklespackages = list(set(versionpicklespackages))

	generatetasks = map(lambda x: (picklehashes[x[0]], x[0], imagedir, pickledir), funcpicklespackages) + map(lambda x: (picklehashes[x[0]], x[0], imagedir, pickledir), versionpicklespackages)

	results = pool.map(generateversionchart, list(set(generatetasks)), 1)
	pool.terminate()

	results = filter(lambda x: x != None, results)

	funcpickletopackage = {}
	for r in funcpicklespackages:
		if funcpickletopackage.has_key(r[0]):
			funcpickletopackage[r[0]].append(r[1])
		else:
			funcpickletopackage[r[0]] = [r[1]]
	
	versionpickletopackage = {}
	for r in versionpicklespackages:
		if versionpickletopackage.has_key(r[0]):
			versionpickletopackage[r[0]].append(r[1])
		else:
			versionpickletopackage[r[0]] = [r[1]]

	## TODO: right now too many results are being copied.
	for r in list(set(results)):
		filehash = r.split('.', 1)[0]
		## remove .png and either -funcversion or -version
		unlinkpickle = True
		for f in pickletofile[filehash]:
			if not funcpickletopackage.has_key(filehash) and not versionpickletopackage.has_key(filehash):
				## this should not happen
				continue
			if versionpickletopackage.has_key(filehash):
				for e in versionpickletopackage[filehash]:
					extension = "version.png"
					filename = "%s-%s-%s" % (f, e, extension)
					if symlinks and len(versionpickletopackage[filehash]) != 1:
						oldcwd = os.getcwd()
                                		os.chdir(imagedir)
                                		os.symlink(r, filename)
                                		os.chdir(oldcwd)
						unlinkpickle = False
					else:
						shutil.copy(os.path.join(imagedir, r), os.path.join(imagedir, filename))
			if funcpickletopackage.has_key(filehash):
				for e in funcpickletopackage[filehash]:
					extension = "funcversion.png"
					filename = "%s-%s-%s" % (f, e, extension)
					if symlinks and len(funcpickletopackage[filehash]) != 1:
						oldcwd = os.getcwd()
                                		os.chdir(imagedir)
                                		os.symlink(r, filename)
                                		os.chdir(oldcwd)
						unlinkpickle = False
					else:
						shutil.copy(os.path.join(imagedir, r), os.path.join(imagedir, filename))
		if unlinkpickle:
			os.unlink(os.path.join(imagedir, r))

	## cleanup
	for i in list(set(map(lambda x: x[0], funcpicklespackages + versionpicklespackages))):
		os.unlink(os.path.join(pickledir, picklehashes[i]))