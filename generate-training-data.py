#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import pandas as pd
import numpy as np
import glob
import sem
import argparse
import shutil
from pathlib import Path

ns_path = './'
script = 'oran-lte-2-lte-ml-handover-train'
campaign_dir = ns_path + 'sem-train'
results_dir = ns_path + 'results-train'
nRuns = 1

def getScenarioParameters(directory):
	parameters = dict()
	subdir = directory.split('/')
	for s in subdir:
		if s.find('=') != -1:
			p = s.split('=')
			if p[1].isnumeric():
				parameters[p[0]] = int(p[1])
			else:
				parameters[p[0]] = p[1]
	parameters["path"] = directory
	return parameters

def calc_distances(point, positions):
	#numpy.linalg.norm(a - b) is equivalent to the euclidean distance
	#between the vectors a and b
	sub = point - positions
	distances = sub.apply(np.linalg.norm, axis=1)
	ncols = len(distances)
	distances.index = [f'distance_{x}' for x in range(1,ncols+1)]
	return distances

ue_query = '''SELECT nodeapploss.nodeid,loss,cellid,x,y,nodeapploss.simulationtime
		FROM nodeapploss
		INNER JOIN lteuecell
		ON nodeapploss.nodeid = lteuecell.nodeid
		AND nodeapploss.simulationtime = lteuecell.simulationtime
		INNER JOIN nodelocation
		ON lteuecell.nodeid = nodelocation.nodeid
		AND lteuecell.simulationtime = nodelocation.simulationtime;'''

enb_query = '''SELECT nodelocation.nodeid,x,y
		FROM nodelocation
		INNER JOIN lteenb
		ON nodelocation.nodeid = lteenb.nodeid
		AND nodelocation.simulationtime = 2000000000'''

parser = argparse.ArgumentParser(description='dataset generation script')
parser.add_argument('-o', '--overwrite', action='store_true',
                    help='Overwrite previous campaign.')
args = parser.parse_args()

campaign = sem.CampaignManager.new(ns_path, script, campaign_dir,
            overwrite=args.overwrite, check_repo=False)
if args.overwrite and Path(results_dir).exists():
	shutil.rmtree(results_dir)

param_combinations = {
		'use-oran': True,
		'use-distance-lm': False,
		'use-onnx-lm': False,
		'use-torch-lm': False,
		'scenario': list(range(3)),
		'start-config': list(range(3)),
		'run-id': list(range(100)),
		'sim-time': 10
		}

campaign.run_missing_simulations(sem.list_param_combinations(param_combinations),
                    runs=nRuns, stop_on_errors=False)

result_param = {
		'scenario': list(range(3)),
		'start-config': list(range(3)),
		'run-id': list(range(100))
		}
if not Path(results_dir).exists():
	campaign.save_to_folders(result_param, results_dir, nRuns)

files = glob.glob(results_dir + 
				  "/scenario=*/start-config=*/run-id=*/run=0/oran-repository.db")

data = []
for f in files:
	parameters = getScenarioParameters(f)
	con = sqlite3.connect(f)
	ue_data = pd.read_sql_query(ue_query, con)

	mean_loss = ue_data.groupby('simulationtime', as_index=False)['loss'].mean()
	mean_loss = mean_loss.rename(columns={'loss': 'mean_loss'})
	ue_data = pd.merge(ue_data, mean_loss, how='right', on='simulationtime')
	ue_data['scenario'] = parameters['scenario']
	ue_data['run-id'] = parameters['run-id']
	ue_data['start-config'] = parameters['start-config']

	data.append(ue_data)
data = pd.concat(data)

con = sqlite3.connect(files[0])
enb_data = pd.read_sql_query(enb_query, con)
distances = data[['x','y']].apply(calc_distances,
									axis='columns',
									result_type='expand',
									positions=enb_data[['x','y']])
serving_cell_distance = distances.values[
						np.arange(len(distances)),data['cellid']-1
						]
data = pd.concat([data, distances], axis='columns')
data['cell_dist'] = serving_cell_distance

data_grouped = data.groupby(['scenario', 'run-id', 'simulationtime'])
optimal = []
for name, group in data_grouped:
	sort = group[group['nodeid'] == 1].sort_values(by=['mean_loss', 'cell_dist'])
	config = sort.iloc[0, sort.columns.get_loc('start-config')]
	optimal.append(group[group['start-config'] == config])
optimal = pd.concat(optimal)

cell_mean = optimal.groupby(['scenario', 'run-id', 'simulationtime', 'cellid'])['loss'].mean()
cell_mean = cell_mean.unstack()
columns = cell_mean.columns.astype(int)
cell_mean.columns = [f'cell_mean_{x}' for x in columns]
cell_mean = cell_mean.reset_index()
optimal = pd.merge(optimal, cell_mean, how='right', on=['scenario', 'run-id', 'simulationtime'])
#optimal = optimal[optimal['nodeid'] == 1].reset_index()

#reorder enbs based on distance
sel = optimal.loc[:,'distance_1':]
sorted_rows = []
for row in sel.itertuples(index=False):
	serving_dist = row[3]
	d = {'distances': row[:3],
		 'losses': row[4:]}
	enbs = pd.DataFrame(d)
	enbs = enbs.sort_values(by='distances')
	enbs = enbs.reset_index(drop=True)
	index = enbs[enbs['distances'] == serving_dist].index[0]
	enbs = enbs.unstack()
	labels = list(row._fields)
	del labels[3]
	enbs.index = labels
	enbs['cellid'] = index
	sorted_rows.append(enbs)
train_data = pd.DataFrame(sorted_rows)

train_data['cellid'] = train_data['cellid'].astype(int)
train_data.insert(6, 'loss', optimal['loss'])

print(train_data)
train_data.iloc[:,:3] = train_data.iloc[:,:3].div(train_data['distance_3'],
												  axis='index')
print(train_data)
train_data.to_csv('training.data', sep=' ', header=False, index=False)
