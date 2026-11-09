"""Reclassify a saved packet without opening a browser or running collection."""
import argparse
import asyncio
from src.services.market_classification_service import classify_market_batch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch_id')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--all',action='store_true',help='Reanalyze all items instead of only failed/pending ones')
    group.add_argument('--excluded',action='store_true',help='Reanalyze excluded items while preserving accepted samples')
    args=parser.parse_args()
    asyncio.run(classify_market_batch(args.batch_id,rerun_all=args.all,reanalyze_excluded=args.excluded))


if __name__=='__main__': main()
