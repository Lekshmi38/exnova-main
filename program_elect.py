import math
import random
import copy
import re
import json
import os
from collections import defaultdict, Counter
import pandas as pd

# Import the modules
from constraint_handler import ConstraintHandler
from rebalancer import Rebalancer

# Initialize modules
constraint_handler = ConstraintHandler()
rebalancer = Rebalancer(constraint_handler)

# ===================== CONFIGURATION =====================
class_group = constraint_handler.CLASS_GROUP
BLOCK_ORDER = constraint_handler.PROGRAM_ELECTIVE_BLOCK_ORDER
COL_CAPACITY = constraint_handler.PROGRAM_ELECTIVE_COL_CAPACITY
ROOM_MAX_CAP = constraint_handler.MAX_TOTAL_ROOM
MAX_SUBJ_PER_COL = constraint_handler.PROGRAM_ELECTIVE_MAX_SUBJ_PER_COL


# ===================== DATA PROCESSING =====================
def extract_elective_counts(data):
    """Use constraint handler for data processing"""
    return constraint_handler.process_program_elective_data(data)


def get_room_metrics(room_dict):
    """Use constraint handler for room metrics"""
    return constraint_handler.get_room_metrics_program_elective(room_dict)


def is_safe(room_data, col_name, subject_name):
    """Use constraint handler for safety checks"""
    return constraint_handler.is_safe_program_elective(room_data, col_name, subject_name)


# ===================== ALLOCATION ENGINE =====================
def generate_allocation(subj_map_orig, elective_counts, num_rooms):
    """Generate room allocation with constraint validation"""
    working_subj_map = copy.deepcopy(subj_map_orig)
    subjects = sorted(
        elective_counts.keys(),
        key=lambda x: elective_counts[x],
        reverse=True
    )

    rooms = {
        f"Room{i}": {blk: [] for blk in BLOCK_ORDER}
        for i in range(1, num_rooms + 1)
    }

    remaining_counts = elective_counts.copy()
    room_class_col_pairing = {
        r_name: defaultdict(lambda: None)
        for r_name in rooms
    }

    def fill_pass(target_cap, variety_limit, max_subjects_per_col=2):
        for sub in subjects:
            if remaining_counts[sub] <= 0:
                continue

            grp = class_group.get(sub.split(':')[0], "OTHER")
            room_keys = list(rooms.keys())
            random.shuffle(room_keys)

            for r_name in room_keys:
                if remaining_counts[sub] <= 0:
                    break

                occ, subjs, grps = get_room_metrics(rooms[r_name])

                if occ >= target_cap:
                    continue
                if sub not in subjs and len(subjs) >= variety_limit:
                    continue
                if sub not in subjs and grp in grps:
                    continue

                for blk in BLOCK_ORDER:
                    if remaining_counts[sub] <= 0 or occ >= target_cap:
                        break

                    col = rooms[r_name][blk]
                    used = len(col)
                    cap = COL_CAPACITY[blk]
                    col_subjects = {s['subj'] for s in col}

                    current_pair_col = room_class_col_pairing[r_name][grp]
                    if current_pair_col is None:
                        max_subj_here = max_subjects_per_col
                    else:
                        max_subj_here = 1

                    if used >= cap:
                        continue
                    if sub not in col_subjects and len(col_subjects) >= max_subj_here:
                        continue

                    # Validate using CHM before placing
                    candidate = {
                        'room': r_name,
                        'block': blk,
                        'subject': sub,
                        'count': 1,
                        'cls': sub.split(':')[0]
                    }

                    is_valid, _ = constraint_handler.validate_allocation(
                        rooms, candidate, 'program_elective'
                    )

                    if not is_valid:
                        continue

                    free = cap - used
                    take = min(free, remaining_counts[sub])

                    if occ + take > target_cap:
                        take = max(1, target_cap - occ)

                    if take > 0:
                        rooms[r_name][blk].extend(working_subj_map[sub][:take])
                        del working_subj_map[sub][:take]
                        remaining_counts[sub] -= take
                        occ += take

                        if max_subj_here == max_subjects_per_col and len(col_subjects) + 1 == max_subjects_per_col:
                            room_class_col_pairing[r_name][grp] = blk

    total_students = sum(elective_counts.values())
    avg_per_room = total_students / num_rooms

    fill_pass(max(25, math.ceil(avg_per_room)), 2)
    fill_pass(max(30, math.ceil(avg_per_room * 1.1)), 3)

    if sum(remaining_counts.values()) > 0:
        fill_pass(ROOM_MAX_CAP, 4, 3)

    return rooms, sum(remaining_counts.values())


# ===================== EXCEL READING =====================
def read_excel_file(file_path):
    """Read Excel file and return data"""
    try:
        print(f"Reading Excel file: {file_path}")

        df = pd.read_excel(file_path)
        df.columns = df.columns.str.strip()

        col_map = {}
        for col in df.columns:
            col_lower = col.lower()
            if any(x in col_lower for x in ['register', 'reg', 'roll']):
                col_map['reg'] = col
            elif 'branch' in col_lower:
                col_map['branch'] = col
            elif any(x in col_lower for x in ['course', 'subject']):
                col_map['course'] = col

        print(f"Found columns: {col_map}")

        if 'reg' not in col_map or 'branch' not in col_map or 'course' not in col_map:
            print("❌ Missing required columns")
            return []

        raw_data = []

        for idx, row in df.iterrows():
            try:
                reg_no = str(row[col_map['reg']]).strip()
                branch_full = str(row[col_map['branch']]).strip()
                course_full = str(row[col_map['course']]).strip()

                if not reg_no or reg_no.upper() == 'NAN':
                    continue
                if not branch_full or branch_full.upper() == 'NAN':
                    continue
                if not course_full or course_full.upper() == 'NAN':
                    continue

                # Extract branch code
                branch_full_upper = branch_full.upper()
                if 'COMPUTER SCIENCE' in branch_full_upper or 'CSE' in branch_full_upper:
                    branch = 'CSE'
                elif 'INFORMATION TECHNOLOGY' in branch_full_upper or 'IT' in branch_full_upper:
                    branch = 'IT'
                elif 'CIVIL' in branch_full_upper:
                    branch = 'CE'
                elif 'ELECTRONICS & COMMUNICATION' in branch_full_upper or 'EC' in branch_full_upper:
                    branch = 'EC'
                elif 'Electronics and Computer' in branch_full_upper or 'ECE' in branch_full_upper:
                    branch = 'ECE'
                elif 'APPLIED ELECTRONICS' in branch_full_upper:
                    branch = 'AE'
                elif 'MECHANICAL' in branch_full_upper:
                    branch = 'ME'
                elif 'ELECTRICAL' in branch_full_upper:
                    branch = 'EE'
                elif 'CHEMICAL' in branch_full_upper:
                    branch = 'CH'
                elif 'BIOTECH' in branch_full_upper:
                    branch = 'BT'
                elif 'MATERIALS' in branch_full_upper:
                    branch = 'MT'
                else:
                    branch = branch_full[:3]

                # Clean course name
                course = re.sub(r'\s*\([^)]*\)', '', course_full).strip()

                raw_data.append((reg_no, branch, course))

            except Exception as e:
                continue

        print(f"✅ Read {len(raw_data)} students from Excel")

        # Show statistics
        subject_counts = Counter([f"{b}:{c}" for _, b, c in raw_data])
        print(f"\nTotal subjects: {len(subject_counts)}")
        print("Subject distribution:")
        for subject, count in subject_counts.most_common(15):
            print(f"  {subject}: {count}")

        return raw_data

    except Exception as e:
        print(f"❌ Error reading Excel: {e}")
        return []


# ===================== REBALANCING =====================
def rebalance_rooms(rooms):
    """Use RBM for rebalancing"""
    rebalanced_rooms, _ = rebalancer.rebalance(rooms, 'program_elective')
    return rebalanced_rooms


# ===================== REPORTING =====================
def save_report_to_files(rooms, original_counts, slot_date_folder, slot_file_path):
    """Save seating arrangement report to files"""
    try:
        total_placed = 0
        arrangement = {
            'rooms': {},
            'summary': {
                'total_rooms': 0,
                'best_leftovers': 0,
                'best_difference': 0,
                'target_difference': 6,
                'actual_difference': 0,
                'average': 0,
                'min': 0,
                'max': 0
            },
            'student_count': sum(original_counts.values()),
            'branch_subject_map': {},
            'elective_type': 'program_elective',
            'qp_summary': {'room_wise': [], 'subject_summary': {}, 'total_students': 0}
        }

        # Save as text report
        report_path = os.path.join(slot_date_folder, 'seating_report.txt')
        with open(report_path, "w", encoding="utf-8") as f:
            active_rooms = {k: v for k, v in rooms.items() if get_room_metrics(v)[0] > 0}
            room_names = sorted(active_rooms.keys(), key=lambda x: int(x.replace("Room", "")))

            arrangement['summary']['total_rooms'] = len(active_rooms)
            room_totals = []

            for r_name in room_names:
                r_data = active_rooms[r_name]
                occ, subjs, _ = get_room_metrics(r_data)
                room_totals.append(occ)
                total_placed += occ

                arrangement['rooms'][r_name] = {
                    'total': occ,
                    'blocks': {},
                    'subjects': list(subjs)
                }

                # Map columns to block names
                block_mapping = {
                    "Col 1": "Left1",
                    "Col 2": "Left3",
                    "Col 3": "Middle2",
                    "Col 4": "Right1",
                    "Col 5": "Right3"
                }

                for blk_orig in BLOCK_ORDER:
                    blk_mapped = block_mapping.get(blk_orig, blk_orig)
                    students = [st['roll'] for st in r_data[blk_orig]]
                    arrangement['rooms'][r_name]['blocks'][blk_mapped] = {
                        'students': students,
                        'capacity': COL_CAPACITY[blk_orig],
                        'count': len(students)
                    }

                # Write room layout
                f.write(f"\n{'=' * 100}\n")
                f.write(f"{r_name.upper()} | TOTAL: {occ} | MAX: {ROOM_MAX_CAP} | SUBJECTS: {len(subjs)}\n")
                f.write(f"{'=' * 100}\n")

                # Column headers
                f.write(f"{'Row':<4} | " + " | ".join([f"{b:<18}" for b in BLOCK_ORDER]) + "\n")
                f.write(f"{'-' * 100}\n")

                # Write rows
                max_rows = 7  # Maximum rows in any column
                for i in range(max_rows):
                    row = f"{i + 1:<4} | "
                    for blk in BLOCK_ORDER:
                        if i < len(r_data[blk]):
                            st = r_data[blk][i]
                            cell = f"{st['roll']}"
                            row += f"{cell:<18} | "
                        elif i < COL_CAPACITY[blk]:
                            row += f"{'--':<18} | "
                        else:
                            row += f"{'':<18} | "
                    f.write(row + "\n")

                f.write(f"{'-' * 100}\n")

                # Subject counts
                stats = Counter([st['subj'] for col in r_data.values() for st in col])
                for s, c in stats.items():
                    f.write(f"  {s}: {c} students\n")

                    arrangement['qp_summary']['room_wise'].append({
                        'Room': r_name, 'Subject': s, 'Student Count': c
                    })

                    if s not in arrangement['qp_summary']['subject_summary']:
                        arrangement['qp_summary']['subject_summary'][s] = 0
                    arrangement['qp_summary']['subject_summary'][s] += c

            f.write(f"\n{'=' * 100}\n")
            f.write(f"SUMMARY\n")
            f.write(f"{'=' * 100}\n")
            f.write(f"Total students placed: {total_placed} / {sum(original_counts.values())}\n")
            f.write(f"Rooms used: {len(active_rooms)}\n")

            if room_totals:
                diff = max(room_totals) - min(room_totals)
                f.write(f"Capacity difference: {diff} (Target: ≤6)\n")
                f.write(f"Average per room: {sum(room_totals) / len(room_totals):.1f}\n")
                f.write(f"Min room: {min(room_totals)}, Max room: {max(room_totals)}\n")

        # Calculate statistics
        if room_totals:
            arrangement['summary']['average'] = sum(room_totals) / len(room_totals)
            arrangement['summary']['min'] = min(room_totals)
            arrangement['summary']['max'] = max(room_totals)
            arrangement['summary']['actual_difference'] = max(room_totals) - min(room_totals)

        arrangement['qp_summary']['total_students'] = total_placed

        # Save JSON
        json_path = os.path.join(slot_date_folder, 'seating_arrangement.json')
        with open(json_path, 'w') as f:
            json.dump(arrangement, f, indent=2)

        # Save QP counts
        qp_path = os.path.join(slot_date_folder, 'qp_counts.txt')
        with open(qp_path, 'w') as f:
            f.write("=" * 60 + "\n")
            f.write("QUESTION PAPER COUNT REPORT\n")
            f.write("=" * 60 + "\n\n")

            f.write("ROOM-WISE DISTRIBUTION\n")
            f.write("-" * 60 + "\n")
            for item in arrangement['qp_summary']['room_wise']:
                f.write(f"{item['Room']}: {item['Subject']} - {item['Student Count']}\n")

            f.write("\n" + "=" * 60 + "\n")
            f.write("SUBJECT SUMMARY\n")
            f.write("=" * 60 + "\n")
            for subject, count in sorted(arrangement['qp_summary']['subject_summary'].items()):
                f.write(f"{subject}: {count}\n")

            f.write(f"\nTOTAL STUDENTS: {arrangement['qp_summary']['total_students']}\n")
            f.write("=" * 60 + "\n")

        return arrangement

    except Exception as e:
        print(f"❌ Error saving reports: {e}")
        import traceback
        traceback.print_exc()
        return None


# ===================== MAIN ALGORITHM =====================
def generate_program_elective_arrangement(slot_file_path, slot_date_folder):
    """Main function for Flask"""
    try:
        print(f"\n{'=' * 80}")
        print(f"PROGRAM ELECTIVE ALGORITHM")
        print(f"Target room difference: ≤6")
        print(f"{'=' * 80}\n")

        # 1. Read Excel file
        raw_data = read_excel_file(slot_file_path)
        if not raw_data:
            return {'error': 'No valid student data found', 'elective_type': 'program_elective'}

        print(f"\n✅ Successfully read {len(raw_data)} students")

        # 2. Process data using constraint handler
        subj_map, elective_counts = constraint_handler.process_program_elective_data(raw_data)
        total_students = sum(elective_counts.values())

        print(f"\n📊 Statistics:")
        print(f"  Total students: {total_students}")
        print(f"  Total subjects: {len(elective_counts)}")

        # Show all subjects
        print(f"\n📚 Subject distribution:")
        for subject, count in sorted(elective_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {subject}: {count}")

        # 3. Validate allocation possibility
        is_possible, message = constraint_handler.validate_allocation_possibility(
            None, total_students, 'program_elective'
        )
        if not is_possible:
            return {'error': message, 'elective_type': 'program_elective'}

        # 4. Calculate room count
        base_rooms = max(8, math.ceil(total_students / 30))
        print(f"\n🏢 Room calculation:")
        print(f"  Base rooms: {total_students} / 30 = {base_rooms}")

        best_allocation = None
        best_diff = float('inf')

        # 5. Try different room counts
        room_counts_to_try = list(range(max(8, base_rooms - 2), min(20, base_rooms + 3)))
        print(f"  Trying room counts: {room_counts_to_try}")

        for rooms_try in room_counts_to_try:
            print(f"\n  Trying {rooms_try} rooms...")

            for attempt in range(300):
                final_rooms, left = generate_allocation(subj_map, elective_counts, rooms_try)

                if left == 0:
                    # Calculate room totals
                    room_totals = []
                    for room_data in final_rooms.values():
                        occ = sum(len(col) for col in room_data.values())
                        if occ > 0:
                            room_totals.append(occ)

                    if room_totals:
                        diff = max(room_totals) - min(room_totals)

                        # Try to rebalance using RBM
                        if diff > 6:
                            final_rooms = rebalancer.rebalance_program_elective(final_rooms, 'program_elective')
                            # Recalculate
                            room_totals = []
                            for room_data in final_rooms.values():
                                occ = sum(len(col) for col in room_data.values())
                                if occ > 0:
                                    room_totals.append(occ)
                            if room_totals:
                                diff = max(room_totals) - min(room_totals)

                        # Check if better
                        if diff < best_diff:
                            best_diff = diff
                            best_allocation = final_rooms.copy()
                            print(f"    ✓ Attempt {attempt + 1}: diff = {diff}")

                            # Good enough?
                            if diff <= 6:
                                print(f"      → Found good solution!")
                                break

                # Progress
                if (attempt + 1) % 100 == 0:
                    print(f"    ... {attempt + 1} attempts")

            # Stop if good solution found
            if best_allocation is not None and best_diff <= 6:
                break

        # 6. Final result
        if best_allocation:
            print(f"\n{'=' * 80}")
            print(f"✅ SUCCESS!")
            print(f"  Rooms used: {len([r for r in best_allocation.values() if get_room_metrics(r)[0] > 0])}")
            print(f"  Capacity difference: {best_diff}")
            print(f"{'=' * 80}\n")

            # 7. Save results
            arrangement = save_report_to_files(best_allocation, elective_counts, slot_date_folder, slot_file_path)

            if arrangement:
                # Add branch-subject mapping
                branch_subject_map = {}
                for subject_key in elective_counts.keys():
                    branch, subject = subject_key.split(':', 1)
                    branch_subject_map[branch] = subject
                arrangement['branch_subject_map'] = branch_subject_map

                return arrangement
            else:
                return {'error': 'Failed to save arrangement files', 'elective_type': 'program_elective'}
        else:
            print(f"\n❌ No valid allocation found")
            return {'error': 'No valid allocation found', 'elective_type': 'program_elective'}

    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'error': str(e), 'elective_type': 'program_elective'}


# ===================== TEST =====================
if __name__ == "__main__":
    # Simple test
    print("Testing program elective algorithm...")

    test_data = []
    for i in range(50):
        test_data.append((f"LBT22CS{i:03d}", "CSE", "PROGRAMMING IN PYTHON"))

    print(f"Test with {len(test_data)} students")

    subj_map, elective_counts = constraint_handler.process_program_elective_data(test_data)
    rooms_try = math.ceil(len(test_data) / 30)

    final_rooms, left = generate_allocation(subj_map, elective_counts, rooms_try)

    if left == 0:
        print(f"✅ Test successful!")
    else:
        print(f"❌ Test failed: {left} students left")