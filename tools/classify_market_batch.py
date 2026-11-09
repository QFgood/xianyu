"""Reclassify a saved packet without opening a browser or running collection."""
import argparse
import asyncio
from src.services.market_classification_service import classify_market_batch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch_id')
    parser.add_argument('--all',action='store_true',help='Reanalyze all items instead of only failed/pending ones')
    args=parser.parse_args()
    asyncio.run(classify_market_batch(args.batch_id,rerun_all=args.all))


if __name__=='__main__': main()
