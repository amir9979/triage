
from __future__ import print_function

import csv
import src.main
import src.utils
import logging
import sys
import scipy.stats

def ap(project, t):
    ranks = src.main.read_ranks(project, t)
    c = project.name+project.version
    new = list()
    for r, i, g in ranks:
        new.append((r, c+str(i), g))

    return new

def print_em(desc, a, b, c, d, ignore=False):
    acc = 6
    x, y = src.main.merge_first_rels(c, a, ignore=ignore)
    T, p = scipy.stats.wilcoxon(x, y)


    changeset_lda = round(src.utils.calculate_mrr(x), acc)
    snapshot_lda = round(src.utils.calculate_mrr(y), acc)
    if p < 0.01:
        changeset_lda = "{\\bf %f }" % changeset_lda
        snapshot_lda = "{\\bf %f }" % snapshot_lda
    else:
        snapshot_lda = "%f" % snapshot_lda
        changeset_lda = "%f" % changeset_lda

    x, y = src.main.merge_first_rels(d, b, ignore=ignore)
    T, p = scipy.stats.wilcoxon(x, y)

    changeset_lsi = round(src.utils.calculate_mrr(x), acc)
    snapshot_lsi = round(src.utils.calculate_mrr(y), acc)
    if p < 0.01:
        changeset_lsi = "{\\bf %f }" % changeset_lsi
        snapshot_lsi = "{\\bf %f }" % snapshot_lsi
    else:
        snapshot_lsi = "%f" % snapshot_lsi
        changeset_lsi = "%f" % changeset_lsi


    l = [desc,
        snapshot_lda, changeset_lda,
        snapshot_lsi, changeset_lsi,
        ]

    print(' & '.join(map(str, l)), '\\\\')

projects = src.main.load_projects()
r_lda = []
r_lsi = []
c_lda = []
c_lsi = []
for project in projects:
    if project.level != sys.argv[1]:
        continue

    desc = ' '.join([project.name, project.version])
    a = ap(project, 'release_lda')
    b = ap(project, 'release_lsi')

    r_lda += a
    r_lsi += b

    c = ap(project, 'changeset_lda')
    d = ap(project, 'changeset_lsi')

    c_lda += c
    c_lsi += d

    print_em(desc, a, b, c, d, ignore=False)


print('\\midrule')
print_em("All", r_lda, r_lsi, c_lda, c_lsi, ignore=False)

print()
print()
print()

r_lda = []
r_lsi = []
t_lda = []
t_lsi = []
for project in projects:
    if project.level != sys.argv[1]:
        continue

    desc = ' '.join([project.name, project.version])
    a = ap(project, 'release_lda')
    b = ap(project, 'release_lsi')

    r_lda += a
    r_lsi += b

    try:
        e = ap(project, 'temporal_lda')
        f = ap(project, 'temporal_lsi')
    except IOError:
        continue

    t_lda += e
    t_lsi += f

    print_em(desc, a, b, e, f, ignore=True)

print('\\midrule')
print_em("All", r_lda, r_lsi, t_lda, t_lsi, ignore=True)
