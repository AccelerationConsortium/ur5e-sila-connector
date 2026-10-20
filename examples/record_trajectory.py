"""Record output-only real-controller joint telemetry; never send motion commands."""

import argparse
import asyncio
import csv
import datetime
import json
import math
import os
import pathlib
import time

from unitelabs.ur5e_connector.io.rtde import JointMonitor


async def record(host: str, output: pathlib.Path, duration: float | None) -> dict:
    """Preserve controller samples and a summary, including on cancellation or failure."""
    monitor = JointMonitor(host)
    summary_path = output.with_suffix('.summary.json')
    if output.exists() or summary_path.exists():
        raise FileExistsError('Choose new CSV and summary paths; existing evidence is never overwritten.')
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {'status': 'opening', 'source': 'output-only controller RTDE', 'samples': 0,
               'collision_or_physical_simulation_validation': False}
    minimum, maximum, peaks = [math.inf]*6, [-math.inf]*6, [0.0]*6
    first = last = None
    gap = 0.0
    # Reserve both outputs before opening the controller connection.
    with output.open('x', newline='') as stream:
        with summary_path.open('x'):
            pass
        writer = csv.writer(stream)
        writer.writerow(['host_utc','controller_seconds',*[f'q{i+1}_degrees' for i in range(6)],
                         *[f'qd{i+1}_degrees_per_second' for i in range(6)],
                         'robot_mode','safety_mode','runtime_state'])
        try:
            await monitor.open()
            started = time.monotonic()
            while duration is None or time.monotonic()-started < duration:
                sample = await monitor.sample()
                if last is not None and sample.timestamp <= last.timestamp:
                    raise ValueError('Controller time did not advance; recording is incomplete.')
                if last is not None:
                    gap = max(gap, sample.timestamp-last.timestamp)
                first = first or sample
                last = sample
                angles = list(map(math.degrees, sample.positions))
                speeds = list(map(math.degrees, sample.velocities))
                for i in range(6):
                    minimum[i] = min(minimum[i], angles[i])
                    maximum[i] = max(maximum[i], angles[i])
                    peaks[i] = max(peaks[i], abs(speeds[i]))
                writer.writerow([datetime.datetime.now(datetime.timezone.utc).isoformat(), sample.timestamp,
                                 *angles, *speeds, sample.robot_mode, sample.safety_mode, sample.runtime_state])
                summary['samples'] += 1
                if summary['samples'] == 1:
                    print('Recording started. No motion commands are sent by this recorder.', flush=True)
                if summary['samples'] % 20 == 0:
                    stream.flush()
                    os.fsync(stream.fileno())
            if first is None:
                raise ValueError('No telemetry samples were received.')
            summary['status'] = 'duration_completed'
        except asyncio.CancelledError:
            summary['status'] = 'operator_stopped_recording'
            raise
        except Exception as error:
            summary.update(status='recording_failed', error=str(error))
            raise
        finally:
            stream.flush()
            os.fsync(stream.fileno())
            await monitor.close()
            if first is not None and last is not None:
                summary.update(controller_elapsed_seconds=last.timestamp-first.timestamp,
                               maximum_sample_gap_seconds=gap,
                               measured_ranges_degrees=[{'min':minimum[i],'max':maximum[i],
                                                         'span':maximum[i]-minimum[i]} for i in range(6)],
                               peak_speeds_degrees_per_second=peaks,
                               final_positions_degrees=list(map(math.degrees,last.positions)),
                               final_velocities_degrees_per_second=list(map(math.degrees,last.velocities)),
                               final_robot_mode=last.robot_mode,final_safety_mode=last.safety_mode,
                               final_runtime_state=last.runtime_state)
            with summary_path.open('w') as summary_stream:
                json.dump(summary,summary_stream,indent=2,allow_nan=False)
                summary_stream.write('\n')
                summary_stream.flush()
                os.fsync(summary_stream.fileno())
            print(f'Recording saved: {output}; {summary["samples"]} samples; {summary["status"]}',flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--robot-host', required=True)
    parser.add_argument('--output', required=True, type=pathlib.Path)
    parser.add_argument('--duration', type=float)
    args = parser.parse_args()
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error('--duration must be finite and positive.')
    try:
        asyncio.run(record(args.robot_host,args.output,args.duration))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
