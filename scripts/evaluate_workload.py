#!/usr/bin/env python3
"""Exercise the real model's video + AI solution workflow in a temporary database.

Requires explicit opt-in for a read-only performance snapshot. No other proposed
computer action is approved. This evaluates the advice workflow, not playback.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from peppermint.common.models import Status
from peppermint.daemon.agent import Agent
from peppermint.daemon.db import Database
from peppermint.daemon.llm import LLM

PROMPT = ('Show me solutions to get my computer to be able to have 1 window running '
          'an X (Twitter) video, 1 window running a YouTube or Netflix video, and '
          '1-2 AI prompts running all at once. Cover both local AI and browser-hosted AI. '
          'Show options; do not change settings.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approve-readonly-snapshot', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    db=Database(':memory:')
    task=db.add_task(PROMPT)
    agent=Agent(db, LLM())
    result=agent.run(task)
    if result.status is Status.AWAITING_CONFIRMATION and result.tool_name == 'performance_snapshot':
        result=agent.resume_after_confirm(task, True)
    passed=(result.status is Status.DONE
            and len(db.get_steps(task))==1
            and db.get_steps(task)[0].tool=='performance_snapshot'
            and db.get_steps(task)[0].status=='ok'
            and all(term in result.text for term in ('Tradeoff:', 'How to verify:', 'Limits:', 'browser-hosted AI')))
    report={'passed':passed,'model':agent.llm.model,'task':db.get_task(task).to_dict(),
            'scope':'Advice workflow only; actual simultaneous video playback was not tested.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2))
    print('PASS' if passed else 'FAIL',result.status.value)
    print(result.text)
    return 0 if passed else 1


if __name__=='__main__':
    raise SystemExit(main())
