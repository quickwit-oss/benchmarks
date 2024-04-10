import argparse
import collections
import json
import os
import pprint
import re
from typing import Any


def check_same_queries(left: list[dict[str, Any]],
                       right: list[dict[str, Any]]):
    """Check that two sets of queries are the same."""

    def by_name(queries):
        ret = {}
        for query in queries:
            name = query['name']
            if name in ret:
                raise ValueError(f'Two queries with the same name {name}')
        ret[name] = query['query']
        return ret

    left_by_name = by_name(left)
    right_by_name = by_name(right)

    diff_names = set(left_by_name) ^ set(right_by_name)
    if diff_names:
        raise ValueError(f'Only one side has the following queries: {diff_names}')

    for name, left_query in left_by_name.items():
        if right_by_name[name] != left_query:
            raise ValueError(
                f'Query "{name}" differs: {pprint.pformat(left_query)} '
                f'vs {pprint.pformat(right_by_name[name])}')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Merges results from multiple indexing and search runs.')
    parser.add_argument(
        '--results-filter', action='extend', nargs='+', type=str,
        help=('If specified, only merge results directories matching (re.search) '
              'the specified regexes'))
    args = parser.parse_args()
    results_filter = None
    if args.results_filter:
        results_filter = [re.compile(regex) for regex in args.results_filter]
    
    results_dir_names = sorted(os.listdir("results"))

    results_by_dataset = collections.defaultdict(list)
    
    for results_dir_name in results_dir_names:
        if results_filter and not any([regex.search(results_dir_name) for regex in results_filter]):
            continue
        indexing_results_filepath = f'results/{results_dir_name}/indexing-results.json'
        search_results_filepath = f'results/{results_dir_name}/search-results.json'
        if not (os.path.isfile(indexing_results_filepath) and os.path.isfile(search_results_filepath)):
            continue
        indexing_results = json.load(open(indexing_results_filepath))
        search_results = json.load(open(search_results_filepath))
        dataset = search_results['track']
        indexing_results['queries'] = search_results['queries']
        indexing_results['track'] = search_results['track']
        indexing_results['dataset'] = search_results['track']
        indexing_results['tag'] = search_results['tag']
        indexing_results['storage'] = search_results['storage']
        indexing_results['instance'] = search_results['instance']
        results_by_dataset[dataset].append(indexing_results)
        print('Added results for dataset', dataset, 'from dir', results_dir_name)

    for dataset_results in results_by_dataset.values():
        queries = []
        for results in dataset_results:
            if queries:
                # Sanity check that all queries are the same, otherwise,
                # the result page will be very misleading.
                check_same_queries(queries, results['queries'])
            else:
                queries = results['queries']
            
    with open("results.json", "w") as results_out:
        json.dump(results_by_dataset, results_out, indent=4)
