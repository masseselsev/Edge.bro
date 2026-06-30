import argparse
import sys
import os
import random
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models

# Add parent directory to path to ensure core imports work correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base
import core.scheduler
from core.scheduler import check_and_trigger_backups

class MockRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        if isinstance(key, bytes):
            key = key.decode("utf-8")
        val = self.store.get(key)
        return val.encode("utf-8") if val else None

    def setex(self, key, time, value):
        if isinstance(key, bytes):
            key = key.decode("utf-8")
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        self.store[key] = str(value)

    def delete(self, key):
        if isinstance(key, bytes):
            key = key.decode("utf-8")
        self.store.pop(key, None)


class MockCeleryTask:
    def __init__(self, simulator):
        self.simulator = simulator

    def delay(self, node_id, comment=""):
        self.simulator.trigger_backup(node_id, comment)


class SchedulerSimulator:
    def __init__(self, num_nodes, days, concurrency_limit, failure_rate, interval, randomize_days, verbose):
        self.num_nodes = num_nodes
        self.days = days
        self.concurrency_limit = concurrency_limit
        self.failure_rate = failure_rate
        self.interval = interval
        self.randomize_days = randomize_days
        self.verbose = verbose

        # Database Setup
        self.engine = create_engine("sqlite:///:memory:")
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()

        # Redis Setup
        self.redis_mock = MockRedis()
        core.scheduler.redis_client = self.redis_mock
        core.scheduler.run_backup_task = MockCeleryTask(self)

        # Simulation State
        self.current_time = datetime(2026, 6, 1, 0, 0, 0)  # Start Monday, June 1, 2026
        self.active_backups = {}  # node_id -> {finish_time, will_fail, comment}
        
        # Statistics
        self.stats_triggers = 0
        self.stats_successes = 0
        self.stats_failures = 0
        self.stats_concurrency_peaks = 0
        self.stats_by_day = {}  # YYYY-MM-DD -> trigger_count

    def populate_db(self):
        # 1. Create a Backup Group
        group = models.BackupGroup(
            name="SimulatedGroup",
            interval=self.interval,
            start_time="02:00",
            end_time="05:00",
            concurrency_limit=self.concurrency_limit,
            randomize_days=self.randomize_days,
            timezone="UTC"
        )
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)

        # 2. Create Nodes
        for i in range(1, self.num_nodes + 1):
            node = models.Node(
                hostname=f"kiosk-{i:03d}",
                ip_address=f"10.222.1.{i}",
                group_id=group.id,
                backup_paused=False
            )
            self.db.add(node)
        self.db.commit()

    def trigger_backup(self, node_id, comment):
        duration = random.randint(15, 45)  # Backups take 15-45 minutes
        will_fail = random.randint(1, 100) <= self.failure_rate
        finish_time = self.current_time + timedelta(minutes=duration)
        
        self.active_backups[node_id] = {
            "finish_time": finish_time,
            "will_fail": will_fail,
            "comment": comment
        }
        
        self.stats_triggers += 1
        day_str = self.current_time.strftime("%Y-%m-%d (Day %w)")
        self.stats_by_day[day_str] = self.stats_by_day.get(day_str, 0) + 1

        if self.verbose:
            node = self.db.query(models.Node).filter(models.Node.id == node_id).first()
            print(f"[{self.current_time}] TRIGGER: {node.hostname} started. Est. duration: {duration}m (Will Fail: {will_fail})")

    def run(self):
        self.populate_db()
        total_minutes = self.days * 24 * 60
        
        print(f"Starting simulation of {self.num_nodes} nodes over {self.days} days...")
        print(f"Group Settings: Interval={self.interval}, Concurrency={self.concurrency_limit}, Randomize={self.randomize_days}")
        print(f"Failure Rate: {self.failure_rate}%")
        print("-" * 80)

        for minute_step in range(total_minutes):
            self.current_time = datetime(2026, 6, 1, 0, 0, 0) + timedelta(minutes=minute_step)
            
            # 1. Process active running backups
            completed = []
            for node_id, info in self.active_backups.items():
                if self.current_time >= info["finish_time"]:
                    completed.append(node_id)
            
            for node_id in completed:
                info = self.active_backups.pop(node_id)
                self.redis_mock.delete(f"backup_running:{node_id}")
                
                # Write to BackupHistory table
                node = self.db.query(models.Node).filter(models.Node.id == node_id).first()
                status = "FAILED" if info["will_fail"] else "SUCCESS"
                
                history = models.BackupHistory(
                    node_id=node_id,
                    archive_name=f"{node.hostname}-{self.current_time.strftime('%Y%m%d-%H%M')}",
                    timestamp=self.current_time,
                    original_size=5 * 1024 * 1024 * 1024,
                    deduplicated_size=3 * 1024 * 1024 * 1024,
                    status=status
                )
                self.db.add(history)
                self.db.commit()

                if status == "SUCCESS":
                    self.stats_successes += 1
                else:
                    self.stats_failures += 1

                if self.verbose:
                    print(f"[{self.current_time}] FINISH: {node.hostname} completed with status: {status}")

            # 2. Run scheduler tick
            check_and_trigger_backups(self.db, now=self.current_time)

            # Record concurrency metrics
            current_concurrency = len(self.active_backups)
            self.stats_concurrency_peaks = max(self.stats_concurrency_peaks, current_concurrency)

        self.report()

    def report(self):
        print("\n" + "=" * 80)
        print("SIMULATION COMPLETED REPORT")
        print("=" * 80)
        print(f"Total Virtual Days:      {self.days}")
        print(f"Total Fleet Size:        {self.num_nodes} nodes")
        print(f"Total Backups Triggered: {self.stats_triggers}")
        print(f"Successful Backups:      {self.stats_successes}")
        print(f"Failed Backups:          {self.stats_failures}")
        print(f"Peak Concurrency Reached:{self.stats_concurrency_peaks}/{self.concurrency_limit}")
        
        # Check missed windows
        missed_nodes = self.db.query(models.Node).filter(models.Node.missed_window == True).all()
        print(f"Nodes with missed window:{len(missed_nodes)}")
        for mn in missed_nodes:
            print(f"  - {mn.hostname} (IP: {mn.ip_address})")

        print("\nDaily Trigger Distribution:")
        print("-" * 30)
        for day, count in sorted(self.stats_by_day.items()):
            bar = "*" * count
            print(f"{day}: {count:3d} | {bar}")
        print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup Scheduler Simulator")
    parser.add_argument("--nodes", type=int, default=15, help="Number of nodes to simulate")
    parser.add_argument("--days", type=int, default=7, help="Number of virtual days to simulate")
    parser.add_argument("--concurrency", type=int, default=3, help="Group concurrency limit")
    parser.add_argument("--failures", type=int, default=10, help="Backup failure rate percentage")
    parser.add_argument("--interval", type=str, default="weekly", choices=["weekly", "monthly", "quarterly", "yearly"])
    parser.add_argument("--randomize", type=str, default="true", choices=["true", "false"])
    parser.add_argument("-v", "--verbose", action="store_true", help="Print verbose step-by-step triggers")
    
    args = parser.parse_args()
    randomize = args.randomize == "true"
    
    sim = SchedulerSimulator(
        num_nodes=args.nodes,
        days=args.days,
        concurrency_limit=args.concurrency,
        failure_rate=args.failures,
        interval=args.interval,
        randomize_days=randomize,
        verbose=args.verbose
    )
    sim.run()
