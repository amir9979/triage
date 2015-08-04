#!/usr/bin/env python2
# -*- coding: utf-8 -*-

from __future__ import print_function

import logging
logger = logging.getLogger('common')

import csv
import os
import os.path
from collections import namedtuple

import dulwich.repo
import scipy.stats
import numpy
from gensim.corpora import MalletCorpus, Dictionary
from gensim.models import LdaModel, LsiModel
from gensim.matutils import sparse2full
from gensim.utils import smart_open, to_unicode, to_utf8

import utils
from corpora import (ChangesetCorpus, SnapshotCorpus, ReleaseCorpus,
                     TaserSnapshotCorpus, TaserReleaseCorpus,
                     CorpusCombiner, GeneralCorpus)
from errors import TaserError
from goldsets import build_goldset, load_goldset
from ownership import build_ownership

def run_basic(project, corpus, other_corpus, queries, goldsets, kind, use_level=False):
    """
    This function runs the experiment in one-shot. It does not evaluate the
    changesets over time.
    """
    logger.info("Running basic evaluation on the %s", kind)
    results = dict()
    if project.lda:
        try:
            lda_ranks = read_ranks(project, kind.lower() + '_lda')
            logger.info("Sucessfully read previously written %s LDA ranks", kind)
            exists = True
        except IOError:
            exists = False

        if project.force or not exists:
            lda_model, _ = create_lda_model(project, corpus, corpus.id2word, kind, use_level=use_level)
            lda_query_topic = get_topics(lda_model, queries)
            lda_doc_topic = get_topics(lda_model, other_corpus)

            lda_ranks = get_rank(lda_query_topic, lda_doc_topic, goldsets)
            write_ranks(project, kind.lower() + '_lda', lda_ranks)

        results['lda'] = get_frms(lda_ranks, goldsets)

    if project.lsi:
        try:
            lsi_ranks = read_ranks(project, kind.lower() + '_lsi')
            logger.info("Sucessfully read previously written %s LSI ranks", kind)
            exists = True
        except IOError:
            exists = False

        if project.force or not exists:
            lsi_model, _ = create_lsi_model(project, corpus, corpus.id2word, kind, use_level=use_level)
            lsi_query_topic = get_topics(lsi_model, queries)
            lsi_doc_topic = get_topics(lsi_model, other_corpus)

            lsi_ranks = get_rank(lsi_query_topic, lsi_doc_topic, goldsets)
            write_ranks(project, kind.lower() + '_lsi', lsi_ranks)

        results['lsi'] = get_frms(lsi_ranks, goldsets)

    return results

def collect_info(project, repos, queries, goldsets, changeset_corpus, release_corpus):
    logger.info("Collecting corpus metdata info")
    changeset_corpus.metadata = True
    release_corpus.metadata = True
    queries.metadata = True
    ids = load_ids(project)
    try:
        issue2git, git2issue = load_issue2git(project, ids)
    except:
        return

    path = os.path.join(project.full_path, "general-info.csv")
    if not os.path.exists(path):
        with smart_open(path, 'w') as f:
            writer = csv.writer(f)
            writer.writerow(["changesets_before_first"])
            row = list()

            for i, docmeta in enumerate(changeset_corpus):
                doc, meta = docmeta
                if meta[0] in git2issue:
                    row.append(i)
                    break

            writer.writerow(row)

    path = os.path.join(project.full_path, 'goldset-info.csv')
    if not os.path.exists(path):
        with smart_open(path, 'w') as f:
            writer = csv.writer(f)
            writer.writerow(["metadata", "total_entities"])

            for gid, goldset in goldsets.items():
                row = list()
                row.append(gid)
                row.append(len(goldset))
                writer.writerow(row)

    collect_helper(project, changeset_corpus, 'changeset')
    collect_helper(project, release_corpus, 'release' + project.level)
    collect_helper(project, queries, 'queries')

    changeset_corpus.metadata = False
    release_corpus.metadata = False
    queries.metadata = False

def collect_helper(project, corpus, name):
    logger.info("Helper corpus metdata info of " + name)
    path = os.path.join(project.full_path, '-'.join([name, 'info.csv']))

    if os.path.exists(path):
        return

    with smart_open(path, 'w') as f:
        writer = csv.writer(f)
        writer.writerow(["metadata", "unique_words", "total_words"])

        for doc, meta in corpus:
            row = list()
            row.append(meta[0])
            row.append(len(doc))
            row.append(sum(freq for word, freq in doc))
            writer.writerow(row)

def write_ranks(project, prefix, ranks):
    with smart_open(os.path.join(project.full_path, '-'.join([prefix, project.level, str(project.num_topics), 'ranks.csv.gz'])), 'w') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'rank', 'distance', 'item'])

        for gid, rank in ranks.items():
            for idx, dist, d_name in rank:
                writer.writerow([gid, idx, dist, to_utf8(d_name)])

def read_ranks(project, prefix):
    ranks = dict()
    with smart_open(os.path.join(project.full_path, '-'.join([prefix, project.level, str(project.num_topics), 'ranks.csv.gz']))) as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for g_id, idx, dist, d_name in reader:
            if g_id not in ranks:
                ranks[g_id] = list()

            ranks[g_id].append((int(idx), float(dist), to_unicode(d_name)))

    return ranks


def run_temporal(project, repos, corpus, queries, goldsets):
    logger.info("Running temporal evaluation")

    force = project.force
    lda_rels = list()
    lsi_rels = list()
    try:
        if project.lda:
            lda_ranks = read_ranks(project, 'temporal')
            lda_rels = get_frms(lda_ranks, goldsets)

        if project.lsi:
            lsi_ranks = read_ranks(project, 'temporal_lsi')
            lsi_rels = get_frms(lsi_ranks, goldsets)

        logger.info("Sucessfully read previously written Temporal ranks")
    except IOError:
        force = True

    if force:
        lda_rels, lsi_rels = run_temporal_helper(project, repos, corpus, queries, goldsets)


    return {'lda': lda_rels, 'lsi': lsi_rels}


def merge_first_rels(a, b, ignore=False):
    first_rels = dict()

    for num, query_id, doc_meta in a:
        #qid = int(query_id)
        qid = query_id
        if qid not in first_rels:
            first_rels[qid] = [num]
        else:
            logger.info('duplicate qid found: %s', query_id)

    for num, query_id, doc_meta in b:
        #qid = int(query_id)
        qid = query_id
        if qid not in first_rels and not ignore:
            logger.info('qid not found: %s', qid)
            first_rels[qid] = [0]

        if qid in first_rels:
            first_rels[qid].append(num)

    for key, v in first_rels.items():
        if len(v) == 1:
            v.append(0)

    x = [v[0] for v in first_rels.values()]
    y = [v[1] for v in first_rels.values()]
    return x, y

def get_frms(ranks, goldsets):
    logger.info('Getting FRMS for %d ranks', len(ranks))
    frms = list()

    for r_id, rank in ranks.items():
        if r_id not in goldsets:
            continue

        for idx, dist, name in rank:
            if name in goldsets[r_id]:
                frms.append((idx, r_id, name))
                break # take only the first one

    logger.info('Returning %d FRMS', len(frms))
    return frms

def get_rels(ranks, goldset=None):
    rels = list()

    if goldset:
        dists = list()
        for name in goldset:
            if name in ranks:
                dists.append((ranks[name], name))

        for dist, name in dists:
            idx = len([1 for x in ranks.values() if x < dist])
            rels.append((idx+1, dist, name))

    else:
        # without the goldset, we have to sort and enumerate all items :(
        sorted_ranks = sorted(ranks.items(), key=lambda x: x[1])
        for idx, rank in enumerate(sorted_ranks):
            d_name, dist = rank
            rels.append((idx+1, dist, d_name))

    rels.sort()

    return rels


def get_rank(query_topic, doc_topic, goldsets=None, distance_measure=utils.hellinger_distance):
    logger.info('Getting ranks between %d query topics and %d doc topics',
                len(query_topic), len(doc_topic))
    ranks = dict()
    for q_meta, query in query_topic:
        qid, _ = q_meta
        q_dist = dict()

        for d_meta, doc in doc_topic:
            d_name, _ = d_meta
            q_dist[d_name] = distance_measure(query, doc)

        if goldsets and qid in goldsets:
            goldset = goldsets[qid]
        else:
            goldset = None

        ranks[qid] = get_rels(q_dist, goldset)

    logger.info('Returning %d ranks', len(ranks))
    return ranks


def get_topics(model, corpus, by_ids=None, full=True):
    logger.info('Getting doc topic for corpus with length %d', len(corpus))
    doc_topic = list()
    corpus.metadata = True
    old_id2word = corpus.id2word
    corpus.id2word = model.id2word

    for doc, metadata in corpus:
        if by_ids is None or metadata[0] in by_ids:
            # get a vector where low topic values are zeroed out.
            topics = model[doc]
            if full:
                topics = sparse2full(topics, model.num_topics)

            # this gets the "full" vector that includes low topic values
            # topics = model.__getitem__(doc, eps=0)
            # topics = [val for id, val in topics]

            doc_topic.append((metadata, topics))

    corpus.metadata = False
    corpus.id2word = old_id2word
    logger.info('Returning doc topic of length %d', len(doc_topic))

    return doc_topic




def load_ids(project):
    with open(os.path.join(project.full_path, 'ids.txt')) as f:
        ids = [x.strip() for x in f.readlines()]

    return ids



def load_issue2git(project, ids):
    logger.info("Loading issue2git.csv")
    dest_fn = os.path.join(project.data_path, 'issue2git.csv')
    if os.path.exists(dest_fn):
        write_out = False
        i2g = dict()
        with open(dest_fn) as f:
            r = csv.reader(f)
            for issue, repo, sha in r:
                if issue not in i2g:
                    i2g[issue] = list()
                i2g[issue].append(sha)

    else:
        write_out = True

        i2s = dict()
        fn = os.path.join(project.full_path, 'IssuesToSVNCommitsMapping.txt')
        with open(fn) as f:
            lines = [line.strip().split('\t') for line in f]
            for line in lines:
                issue = line[0]
                links = line[1]
                svns = line[2:]

                i2s[issue] = svns

        s2g = dict()
        fn = os.path.join(project.data_path, 'svn2git.csv')
        with open(fn) as f:
            reader = csv.reader(f)
            for svn,git in reader:
                if svn in s2g and s2g[svn] != git:
                    logger.info('Different gits sha for SVN revision number %s', svn)
                else:
                    s2g[svn] = git

        i2g = dict()
        for issue, svns in i2s.items():
            for svn in svns:
                if svn in s2g:
                    # make sure we don't have issues that are empty
                    if issue not in i2g:
                        i2g[issue] = list()
                    i2g[issue].append(s2g[svn])
                else:
                    logger.info('Could not find git sha for SVN revision number %s', svn)

    logger.info("Loaded issue2git with %d entries", len(i2g))

    # Make sure we have a commit for all issues
    keys = set(i2g.keys())
    ignore = set(ids) - keys
    if len(ignore):
        logger.info("Ignoring evaluation for the following issues:\n\t%s",
                    '\n\t'.join(ignore))

    # build reverse mapping
    g2i = dict()
    for issue, gits in i2g.items():
        for git in gits:
            if git not in g2i:
                g2i[git] = list()
            g2i[git].append(issue)

    if write_out:
        with open(dest_fn, 'w') as f:
            w = csv.writer(f)
            for issue, gits in i2g.items():
                w.writerow([issue] + gits)

    logger.info("Returning issue2git with len %d and git2issue with len %d", len(i2g), len(g2i))

    return i2g, g2i


def load_projects(config):
    projects = list()
    refpaths = list()
    for dirpath, dirname, filenames in os.walk('data'):
        for filename in filenames:
            if filename == 'ref':
                refpaths.append(os.path.join(dirpath, filename))

    for refpath in refpaths:
        with open(refpath) as f:
            ref = f.read().strip()

        # extract project info based on path, weeee
        full_path, _ = os.path.split(refpath)
        data_path, project_version = os.path.split(full_path)
        _, project_name = os.path.split(data_path)
        src_path = os.path.join(full_path, 'src')

        Project = namedtuple('Project',  ' '.join(['name', 'printable_name', 'version', 'ref', 'data_path', 'full_path', 'src_path']
                                                  + config.keys()))
        # figure out which column index contains the project name

        # find the project in the csv, adding it's info to config
        # do the os.path.join to force a trailing slash
        row = [project_name, project_name + ' ' + project_version, project_version, ref,
               os.path.join(data_path, ''),
               os.path.join(full_path, ''),
               os.path.join(src_path, '')]
        row += config.values()

        projects.append(Project(*row))

    return projects


def load_repos(project):
    # reading in repos
    with open(os.path.join('data', project.name, 'repos.txt')) as f:
        repo_urls = [line.strip() for line in f]

    repos_base = 'gits'
    if not os.path.exists(repos_base):
        utils.mkdir(repos_base)

    repos = list()

    for url in repo_urls:
        repo_name = url.split('/')[-1]
        target = os.path.join(repos_base, repo_name)
        try:
            repo = utils.clone(url, target, bare=True)
        except OSError:
            repo = dulwich.repo.Repo(target)

        repos.append(repo)

    return repos


def create_queries(project):
    corpus_fname_base = project.full_path + 'Queries'
    corpus_fname = corpus_fname_base + '.mallet.gz'
    dict_fname = corpus_fname_base + '.dict.gz'

    if not os.path.exists(corpus_fname):
        pp = GeneralCorpus(lazy_dict=True)
        id2word = Dictionary()

        ids = load_ids(project)

        queries = list()
        for id in ids:
            with open(os.path.join(project.data_path, 'queries',
                                    'short', '%s.txt' % id)) as f:
                short = f.read()

            with open(os.path.join(project.data_path, 'queries',
                                    'long', '%s.txt' % id)) as f:
                long = f.read()

            text = ' '.join([short, long])
            text = pp.preprocess(text)

            # this step will remove any words not found in the dictionary
            bow = id2word.doc2bow(text, allow_update=True)

            queries.append((bow, (id, 'query')))

        # write the corpus and dictionary to disk. this will take awhile.
        MalletCorpus.serialize(corpus_fname, queries, id2word=id2word,
                               metadata=True)

    # re-open the compressed versions of the dictionary and corpus
    id2word = None
    if os.path.exists(dict_fname):
        id2word = Dictionary.load(dict_fname)

    corpus = MalletCorpus(corpus_fname, id2word=id2word)

    return corpus


def create_lda_model(project, corpus, id2word, name, use_level=True, force=False):
    model_fname = project.full_path + name + str(project.num_topics)
    if use_level:
        model_fname += project.level

    model_fname += '.lda.gz'


    if not os.path.exists(model_fname) or project.force or force:
        if corpus:
            update_every=None # run in batch if we have a pre-supplied corpus
        else:
            update_every=1

        model = LdaModel(corpus=corpus,
                         id2word=id2word,
                         alpha=project.alpha,
                         eta=project.eta,
                         chunksize=project.chunksize,
                         passes=project.passes,
                         num_topics=project.num_topics,
                         iterations=project.iterations,
                         eval_every=None, # disable perplexity tests for speed
                         update_every=update_every,
                         )

        if corpus:
            model.save(model_fname)
    else:
        model = LdaModel.load(model_fname)

    return model, model_fname

def create_lsi_model(project, corpus, id2word, name, use_level=True, force=False):
    model_fname = project.full_path + name + str(project.num_topics)
    if use_level:
        model_fname += project.level

    model_fname += '.lsi.gz'

    if not os.path.exists(model_fname) or project.force or force:
        model = LsiModel(corpus=corpus,
                         id2word=id2word,
                         num_topics=project.num_topics,
                         )

        if corpus:
            model.save(model_fname)
    else:
        model = LsiModel.load(model_fname)

    return model, model_fname


def create_mallet_model(project, corpus, name, use_level=True):
    model_fname = project.full_path + name + str(project.num_topics)
    if use_level:
        model_fname += project.level

    model_fname += '.malletlda.gz'

    if not os.path.exists(model_fname):
        model = LdaMallet('./lib/mallet-2.0.7/bin/mallet',
                          corpus=corpus,
                          id2word=corpus.id2word,
                          optimize_interval=10,
                          num_topics=project.num_topics)

        model.save(model_fname)
    else:
        model = LdaMallet.load(model_fname)

    return model


def create_corpus(project, repos, Kind, use_level=True, forced_ref=None):
    corpus_fname_base = project.full_path + Kind.__name__

    if use_level:
        corpus_fname_base += project.level

    if forced_ref:
        corpus_fname_base += forced_ref[:8]

    corpus_fname = corpus_fname_base + '.mallet.gz'
    dict_fname = corpus_fname_base + '.dict.gz'
    made_one = False

    if not os.path.exists(corpus_fname):
        combiner = CorpusCombiner()

        for repo in repos:
            try:
                if repo or forced_ref:
                    corpus = Kind(project=project,
                                  repo=repo,
                                  lazy_dict=True,
                                  ref=forced_ref,
                                  )
                else:
                    corpus = Kind(project=project, lazy_dict=True)

            except KeyError:
                continue
            except TaserError as e:
                if repo == repos[-1] and not made_one:
                    raise e
                    # basically, if we are at the last repo and we STILL
                    # haven't sucessfully extracted a corpus, ring some bells
                else:
                    # otherwise, keep trying. winners never quit.
                    continue

            combiner.add(corpus)
            made_one = True

        # write the corpus and dictionary to disk. this will take awhile.
        combiner.metadata = True
        MalletCorpus.serialize(corpus_fname, combiner, id2word=combiner.id2word,
                               metadata=True)
        combiner.metadata = False

        # write out the dictionary
        combiner.id2word.save(dict_fname)

    # re-open the compressed versions of the dictionary and corpus
    id2word = None
    if os.path.exists(dict_fname):
        id2word = Dictionary.load(dict_fname)

    corpus = MalletCorpus(corpus_fname, id2word=id2word)

    return corpus


def create_release_corpus(project, repos, forced_ref=None):
    if project.level == 'file':
        RC = ReleaseCorpus
        SC = SnapshotCorpus
    else:
        RC = TaserReleaseCorpus
        SC = TaserSnapshotCorpus

    return create_corpus(project, repos, SC, forced_ref=forced_ref)

    # not using this just yet
    if forced_ref:
        return create_corpus(project, repos, SC, forced_ref=forced_ref)
    else:
        try:
            return create_corpus(project, [None], RC)
        except TaserError:
            return create_corpus(project, repos, SC, forced_ref=forced_ref)