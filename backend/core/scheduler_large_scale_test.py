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
import logging
logging.getLogger("core.scheduler").setLevel(logging.ERROR)
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


class LargeScaleSimulator:
    def __init__(self, days=30):
        self.days = days
        self.num_nodes = 300

        # Database Setup
        self.engine = create_engine("sqlite:///:memory:")
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()
        with self.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_history ON backup_history (node_id, status, timestamp)"))

        # Redis Setup
        self.redis_mock = MockRedis()
        core.scheduler.redis_client = self.redis_mock
        core.scheduler.run_backup_task = MockCeleryTask(self)

        # Simulation State
        self.current_time = datetime(2026, 1, 1, 0, 0, 0)  # Start Thursday, January 1, 2026
        self.active_backups = {}  # node_id -> {finish_time, will_fail, group_id}
        
        # Statistics
        self.stats_triggers = 0
        self.stats_successes = 0
        self.stats_failures = 0
        self.stats_concurrency_peaks = {}  # group_id -> max_concurrency
        self.stats_by_day = {}  # YYYY-MM-DD -> trigger_count
        self.group_stats = {}  # group_id -> {triggers, successes, failures, missed}

        # Defined Groups Configuration
        self.group_configs = [
            {
                "id": 1,
                "name": "Weekly Production (UTC)",
                "interval": "weekly",
                "target_week": 1,
                "start_time": "02:00",
                "end_time": "05:00",
                "concurrency_limit": 5,
                "randomize_days": True,
                "timezone": "UTC",
                "upload_rate_limit": 10240,  # 10 MB/s
                "compression": "zstd:3",
                "cpu_quota": 50,
                "failure_rate": 5
            },
            {
                "id": 2,
                "name": "Weekly MSK (UTC+3)",
                "interval": "weekly",
                "target_week": 1,
                "start_time": "01:00",
                "end_time": "03:00",
                "concurrency_limit": 3,
                "randomize_days": False,
                "timezone": "Europe/Moscow",
                "upload_rate_limit": 5120,   # 5 MB/s
                "compression": "lz4",
                "cpu_quota": 30,
                "failure_rate": 8
            },
            {
                "id": 3,
                "name": "Monthly EST (UTC-5)",
                "interval": "monthly",
                "target_week": 1,
                "start_time": "03:00",
                "end_time": "06:00",
                "concurrency_limit": 4,
                "randomize_days": True,
                "timezone": "EST",
                "upload_rate_limit": 2048,   # 2 MB/s
                "compression": "zstd:9",
                "cpu_quota": 70,
                "failure_rate": 10
            },
            {
                "id": 4,
                "name": "Monthly CET (UTC+1, Cross-Midnight)",
                "interval": "monthly",
                "target_week": 2,
                "start_time": "22:00",
                "end_time": "02:00",
                "concurrency_limit": 2,
                "randomize_days": True,
                "timezone": "Europe/Berlin",
                "upload_rate_limit": None,   # Unlimited
                "compression": None,         # Default
                "cpu_quota": None,           # Default
                "failure_rate": 12
            },
            {
                "id": 5,
                "name": "Quarterly UTC (UTC)",
                "interval": "quarterly",
                "target_week": 1,
                "start_time": "00:00",
                "end_time": "04:00",
                "concurrency_limit": 6,
                "randomize_days": True,
                "timezone": "UTC",
                "upload_rate_limit": 20480,  # 20 MB/s
                "compression": "zstd:1",
                "cpu_quota": 90,
                "failure_rate": 15
            },
            {
                "id": 6,
                "name": "Yearly UTC (UTC)",
                "interval": "yearly",
                "target_week": 1,
                "start_time": "04:00",
                "end_time": "08:00",
                "concurrency_limit": 5,
                "randomize_days": True,
                "timezone": "UTC",
                "upload_rate_limit": 1024,   # 1 MB/s
                "compression": "zlib",
                "cpu_quota": 20,
                "failure_rate": 20
            }
        ]

    def populate_db(self):
        # 1. Create groups in database
        for config in self.group_configs:
            group = models.BackupGroup(
                id=config["id"],
                name=config["name"],
                interval=config["interval"],
                target_week=config["target_week"],
                start_time=config["start_time"],
                end_time=config["end_time"],
                concurrency_limit=config["concurrency_limit"],
                randomize_days=config["randomize_days"],
                timezone=config["timezone"],
                upload_rate_limit=config["upload_rate_limit"],
                compression=config["compression"],
                cpu_quota=config["cpu_quota"]
            )
            self.db.add(group)
            self.group_stats[config["id"]] = {
                "triggers": 0,
                "successes": 0,
                "failures": 0,
                "missed": 0,
                "running_max": 0
            }
            self.stats_concurrency_peaks[config["id"]] = 0
            
        self.db.commit()

        # 2. Add 300 nodes (50 nodes per group)
        for g_idx, config in enumerate(self.group_configs):
            g_id = config["id"]
            for i in range(1, 51):
                node_num = g_idx * 50 + i
                node = models.Node(
                    id=node_num,
                    hostname=f"kiosk-{node_num:03d}",
                    ip_address=f"10.222.{g_id}.{i}",
                    group_id=g_id,
                    backup_paused=False
                )
                self.db.add(node)
        self.db.commit()

    def trigger_backup(self, node_id, comment):
        node = self.db.query(models.Node).filter(models.Node.id == node_id).first()
        g_id = node.group_id
        config = self.group_configs[g_id - 1]

        duration = random.randint(15, 45)  # Backups take 15-45 minutes
        will_fail = random.randint(1, 100) <= config["failure_rate"]
        finish_time = self.current_time + timedelta(minutes=duration)
        
        self.active_backups[node_id] = {
            "finish_time": finish_time,
            "will_fail": will_fail,
            "group_id": g_id
        }
        
        self.stats_triggers += 1
        self.group_stats[g_id]["triggers"] += 1
        
        day_str = self.current_time.strftime("%Y-%m-%d")
        self.stats_by_day[day_str] = self.stats_by_day.get(day_str, 0) + 1

    def run(self):
        self.populate_db()
        total_minutes = self.days * 24 * 60

        print(f"Running simulation of 300 nodes across 6 groups for {self.days} days...")

        for minute_step in range(total_minutes):
            self.current_time = datetime(2026, 1, 1, 0, 0, 0) + timedelta(minutes=minute_step)
            
            # 1. Process active running backups
            completed = []
            for node_id, info in self.active_backups.items():
                if self.current_time >= info["finish_time"]:
                    completed.append(node_id)
            
            for node_id in completed:
                info = self.active_backups.pop(node_id)
                self.redis_mock.delete(f"backup_running:{node_id}")
                
                status = "FAILED" if info["will_fail"] else "SUCCESS"
                g_id = info["group_id"]
                
                node = self.db.query(models.Node).filter(models.Node.id == node_id).first()
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
                    self.group_stats[g_id]["successes"] += 1
                else:
                    self.stats_failures += 1
                    self.group_stats[g_id]["failures"] += 1

            # 2. Run scheduler tick
            check_and_trigger_backups(self.db, now=self.current_time)

            # Prevent SQLAlchemy session bloat by close/re-opening the session periodically
            if minute_step % 60 == 0:
                self.db.close()
                self.db = self.SessionLocal()

            # Record concurrency metrics per group
            group_running = {g["id"]: 0 for g in self.group_configs}
            for b in self.active_backups.values():
                group_running[b["group_id"]] += 1

            for g_id, running_count in group_running.items():
                self.stats_concurrency_peaks[g_id] = max(self.stats_concurrency_peaks[g_id], running_count)

        # Record missed windows count
        missed_nodes = self.db.query(models.Node).filter(models.Node.missed_window == True).all()
        for mn in missed_nodes:
            self.group_stats[mn.group_id]["missed"] += 1

        self.generate_html_report(missed_nodes)

    def generate_html_report(self, missed_nodes):
        os.makedirs("reports", exist_ok=True)
        report_path = "reports/large_scale_test_report.html"

        success_rate = (self.stats_successes / self.stats_triggers * 100) if self.stats_triggers > 0 else 0.0

        # Generate Group Stats Rows
        group_rows = ""
        for config in self.group_configs:
            g_id = config["id"]
            stats = self.group_stats[g_id]
            g_success_rate = (stats["successes"] / stats["triggers"] * 100) if stats["triggers"] > 0 else 0.0
            
            group_rows += f"""
            <tr class="border-b border-zinc-800/80 hover:bg-zinc-850/30 transition-colors">
                <td class="px-4 py-3.5 text-zinc-100 font-medium">{config['name']}</td>
                <td class="px-4 py-3.5 text-zinc-400 font-mono text-[11px] uppercase">{config['interval']}</td>
                <td class="px-4 py-3.5 text-zinc-400 font-mono text-[11px]">{config['start_time']} - {config['end_time']}</td>
                <td class="px-4 py-3.5 text-zinc-400 font-mono text-[11px]">{config['timezone']}</td>
                <td class="px-4 py-3.5 text-center font-mono text-[11px] text-zinc-300">{stats['triggers']}</td>
                <td class="px-4 py-3.5 text-center font-mono text-[11px] text-emerald-400 font-bold">{stats['successes']}</td>
                <td class="px-4 py-3.5 text-center font-mono text-[11px] text-rose-400">{stats['failures']}</td>
                <td class="px-4 py-3.5 text-center font-mono text-[11px] { 'text-rose-400 font-bold' if stats['missed'] > 0 else 'text-zinc-500' }">{stats['missed']}</td>
                <td class="px-4 py-3.5 text-center font-mono text-[11px] text-indigo-400">{self.stats_concurrency_peaks[g_id]}/{config['concurrency_limit']}</td>
                <td class="px-4 py-3.5 text-right font-mono text-[11px] text-emerald-400 font-bold">{g_success_rate:.1f}%</td>
            </tr>
            """

        # Generate Timeline Rows
        timeline_rows = ""
        for day in sorted(self.stats_by_day.keys()):
            count = self.stats_by_day[day]
            width = min(100, (count / max(1, max(self.stats_by_day.values()))) * 100)
            timeline_rows += f"""
            <div class="flex items-center gap-4 text-xs font-mono text-zinc-400 py-1.5 border-b border-zinc-800/40">
                <span class="w-24 shrink-0">{day}</span>
                <span class="w-8 text-right shrink-0 text-zinc-300">{count}</span>
                <div class="flex-1 bg-zinc-950 h-3 rounded overflow-hidden">
                    <div class="bg-indigo-600 h-full rounded transition-all duration-500" style="width: {width}%"></div>
                </div>
            </div>
            """

        # Generate Missed Windows Rows
        missed_rows = ""
        for mn in missed_nodes:
            config = self.group_configs[mn.group_id - 1]
            missed_rows += f"""
            <tr class="border-b border-zinc-800/80 hover:bg-rose-950/10 transition-colors">
                <td class="px-4 py-3 text-rose-400 font-medium">{mn.hostname}</td>
                <td class="px-4 py-3 text-zinc-400 font-mono text-[11px]">{mn.ip_address}</td>
                <td class="px-4 py-3 text-zinc-300">{config['name']}</td>
                <td class="px-4 py-3 text-zinc-400 font-mono text-[11px]">{config['start_time']} - {config['end_time']}</td>
                <td class="px-4 py-3 text-right text-rose-400 text-xs">Exceeded Limit / Network Offline</td>
            </tr>
            """
        if not missed_nodes:
            missed_rows = """
            <tr>
                <td colspan="5" class="px-4 py-8 text-center text-zinc-500 font-medium">No nodes missed their execution windows during simulation.</td>
            </tr>
            """

        # HTML Template
        html = f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Automated Backup Scheduler - Large Scale Test Report</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <style>
        body {{
            background-color: #09090b;
            color: #e4e4e7;
            font-family: 'Inter', sans-serif;
            margin: 0;
            padding: 2.5rem 1.5rem;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            margin-bottom: 2rem;
        }}
        .header h1 {{
            font-size: 1.8rem;
            font-weight: 700;
            color: #f4f4f5;
            margin: 0 0 0.5rem 0;
        }}
        .header p {{
            font-size: 0.9rem;
            color: #a1a1aa;
            margin: 0;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background-color: #18181b;
            border: 1px border #27272a;
            border-radius: 0.75rem;
            padding: 1.25rem;
            display: flex;
            flex-direction: column;
        }}
        .card-label {{
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            color: #71717a;
            letter-spacing: 0.05em;
        }}
        .card-value {{
            font-size: 1.8rem;
            font-weight: 700;
            color: #f4f4f5;
            margin-top: 0.5rem;
            font-family: 'JetBrains Mono', monospace;
        }}
        .card-secondary {{
            font-size: 0.75rem;
            color: #a1a1aa;
            margin-top: 0.25rem;
        }}
        .section {{
            background-color: #18181b;
            border: 1px solid #27272a;
            border-radius: 0.75rem;
            padding: 1.5rem;
            margin-bottom: 2rem;
        }}
        .section-title {{
            font-size: 1rem;
            font-weight: 600;
            color: #f4f4f5;
            margin-top: 0;
            margin-bottom: 1.25rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}
        th {{
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            color: #71717a;
            letter-spacing: 0.05em;
            padding: 0.75rem 1rem;
            border-b: 1px solid #27272a;
        }}
        td {{
            font-size: 0.8rem;
            padding: 0.75rem 1rem;
        }}
        .border-b {{
            border-bottom: 1px solid #27272a;
        }}
        .text-center {{ text-align: center; }}
        .text-right {{ text-align: right; }}
        .text-rose-400 {{ color: #fb7185; }}
        .text-emerald-400 {{ color: #34d399; }}
        .text-indigo-400 {{ color: #818cf8; }}
        .text-zinc-500 {{ color: #52525b; }}
        .font-mono {{ font-family: 'JetBrains Mono', monospace; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Scheduler Stress-Testing Simulation Report</h1>
            <p>Automated backup execution audit log over {self.days} days simulating 300 connected kiosks.</p>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-label">Total Simulated Nodes</div>
                <div class="card-value">300</div>
                <div class="card-secondary">50 nodes per group</div>
            </div>
            <div class="card">
                <div class="card-label">Total Runs Triggered</div>
                <div class="card-value">{self.stats_triggers}</div>
                <div class="card-secondary">{self.stats_successes} Successful • {self.stats_failures} Failed</div>
            </div>
            <div class="card">
                <div class="card-label">Overall Success Rate</div>
                <div class="card-value text-emerald-400">{success_rate:.1f}%</div>
                <div class="card-secondary">Average fail probability factored</div>
            </div>
            <div class="card">
                <div class="card-label">Nodes Missed Window</div>
                <div class="card-value { 'text-rose-400' if len(missed_nodes) > 0 else '' }">{len(missed_nodes)}</div>
                <div class="card-secondary">Over-concurrency / persistent offline</div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">
                <span>Backup Group Configurations & Execution Statistics</span>
            </div>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Group Name</th>
                            <th>Interval</th>
                            <th>Time Window</th>
                            <th>Timezone</th>
                            <th class="text-center">Triggers</th>
                            <th class="text-center">Successes</th>
                            <th class="text-center">Failures</th>
                            <th class="text-center">Missed</th>
                            <th class="text-center">Peak Concurrency</th>
                            <th class="text-right">Success Rate</th>
                        </tr>
                    </thead>
                    <tbody>
                        {group_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Daily Execution Timeline (Backup Load)</div>
            <div style="display: flex; flex-direction: column; gap: 0.25rem;">
                {timeline_rows}
            </div>
        </div>

        <div class="section" style="border-color: #e11d4820;">
            <div class="section-title" style="color: #fb7185;">Missed Backup Windows Registry</div>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Hostname</th>
                            <th>IP Address</th>
                            <th>Group</th>
                            <th>Allowed Time Window</th>
                            <th class="text-right">Failure Reason</th>
                        </tr>
                    </thead>
                    <tbody>
                        {missed_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</body>
</html>
"""
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"HTML Report generated successfully at: {report_path}")


if __name__ == "__main__":
    sim = LargeScaleSimulator(days=7)
    sim.run()
