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

# ===================== CONFIGURATION =====================
BLOCK_ORDER = ["Col 1", "Col 2", "Col 3", "Col 4", "Col 5"]

# Map column names to display names expected by template
BLOCK_MAPPING = {
    "Col 1": "Left1",
    "Col 2": "Left3",
    "Col 3": "Middle2",
    "Col 4": "Right1",
    "Col 5": "Right3"
}

COL_CAPACITY = {
    "Col 1": 7,
    "Col 2": 7,
    "Col 3": 6,
    "Col 4": 7,
    "Col 5": 7
}

ROOM_MAX_CAP = 34  # 7+7+6+7+7 = 34
MAX_SUBJ_PER_COL = 2

# Initialize modules
constraint_handler = ConstraintHandler()
rebalancer = Rebalancer(constraint_handler)


# ===================== DATA PROCESSING =====================
def extract_elective_counts(data):
    """Extract subject-wise student data - for open electives, key is just subject name"""
    subj_map = defaultdict(list)
    counts = Counter()

    for roll, branch, subject in data:
        # Clean subject name - remove course code in parentheses for grouping
        clean_subject = re.sub(r'\s*\([^)]*\)', '', subject).strip()
        key = clean_subject
        subj_map[key].append({"roll": roll, "branch": branch, "subj": key, "full_subj": subject})
        counts[key] += 1

    # Sort students within each subject by roll number
    for key in subj_map:
        subj_map[key].sort(key=lambda x: x['roll'])

    return subj_map, dict(counts)


def get_room_metrics(room_dict):
    """Get room occupancy and subjects"""
    occ = sum(len(col) for col in room_dict.values())
    subjs = {st['subj'] for col in room_dict.values() for st in col}
    return occ, subjs


def is_safe(room_data, col_name, subject_name):
    """Check if placing subject in column is safe (no adjacent same subject) using constraint handler"""
    if col_name not in room_data:
        return True

    # Use constraint handler's open elective safety check
    return constraint_handler.is_safe_open_elective(room_data, col_name, subject_name)


# ===================== ALLOCATION ENGINE =====================
def generate_allocation(subj_map_orig, elective_counts, num_rooms):
    """Generate room allocation for open electives"""
    # Create a fresh deep copy for this allocation attempt
    working_subj_map = copy.deepcopy(subj_map_orig)

    # Create a list of subjects sorted by count (largest first)
    subjects = sorted(
        elective_counts.keys(),
        key=lambda x: elective_counts[x],
        reverse=True
    )

    # Initialize rooms
    rooms = {
        f"Room{i}": {blk: [] for blk in BLOCK_ORDER}
        for i in range(1, num_rooms + 1)
    }

    # Track remaining counts
    remaining_counts = elective_counts.copy()

    # Track which column already has 2 subjects
    room_col_pairing = {
        r_name: defaultdict(lambda: None)
        for r_name in rooms
    }

    def fill_pass(target_cap, variety_limit, max_subjects_per_col=2):
        for sub in subjects:
            if remaining_counts[sub] <= 0:
                continue

            room_keys = list(rooms.keys())
            random.shuffle(room_keys)

            for r_name in room_keys:
                if remaining_counts[sub] <= 0:
                    break

                occ, subjs = get_room_metrics(rooms[r_name])

                if occ >= target_cap:
                    continue
                if sub not in subjs and len(subjs) >= variety_limit:
                    continue

                for blk in BLOCK_ORDER:
                    if remaining_counts[sub] <= 0 or occ >= target_cap:
                        break

                    col = rooms[r_name][blk]
                    used = len(col)
                    cap = COL_CAPACITY[blk]
                    col_subjects = {s['subj'] for s in col}

                    # Determine if this column can accept another subject
                    if room_col_pairing[r_name][blk] is None:
                        max_subj_here = max_subjects_per_col
                    else:
                        max_subj_here = 1

                    if used >= cap:
                        continue
                    if sub not in col_subjects and len(col_subjects) >= max_subj_here:
                        continue

                    # Validate using constraint handler
                    candidate = {
                        'room': r_name,
                        'block': blk,
                        'subject': sub,
                        'count': 1
                    }
                    is_valid, _ = constraint_handler.validate_allocation(
                        rooms, candidate, 'open_elective'
                    )

                    if not is_valid:
                        continue

                    # Assign students
                    free = cap - used
                    take = min(free, remaining_counts[sub])

                    if occ + take > target_cap:
                        take = max(1, target_cap - occ)

                    if take > 0 and sub in working_subj_map and len(working_subj_map[sub]) >= take:
                        # Add students to room
                        students_to_add = working_subj_map[sub][:take]
                        rooms[r_name][blk].extend(students_to_add)

                        # Remove from working map
                        del working_subj_map[sub][:take]

                        # Update counts
                        remaining_counts[sub] -= take
                        occ += take

                        # Mark this column as having 2 subjects if applicable
                        if max_subj_here == max_subjects_per_col and len(col_subjects) + 1 == max_subjects_per_col:
                            room_col_pairing[r_name][blk] = True

    total_students = sum(elective_counts.values())
    avg_per_room = total_students / num_rooms if num_rooms > 0 else 0

    # Multiple passes with increasing targets
    if num_rooms > 0:
        # First pass: try to fill rooms evenly
        fill_pass(min(ROOM_MAX_CAP, math.ceil(avg_per_room)), 2)

        # Second pass: fill remaining to 80% capacity
        fill_pass(min(ROOM_MAX_CAP, math.ceil(avg_per_room * 1.2)), 3)

        # Third pass: fill to 90% capacity
        fill_pass(min(ROOM_MAX_CAP, math.ceil(avg_per_room * 1.5)), 4, 3)

        # Final pass: fill completely with more variety allowed
        if sum(remaining_counts.values()) > 0:
            fill_pass(ROOM_MAX_CAP, 10, 3)

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
                if 'COMPUTER SCIENCE' in branch_full_upper:
                    branch = 'CSE'
                elif 'INFORMATION TECHNOLOGY' in branch_full_upper:
                    branch = 'IT'
                elif 'CIVIL' in branch_full_upper:
                    branch = 'CE'
                elif 'ELECTRONICS & COMMUNICATION' in branch_full_upper:
                    branch = 'EC'
                elif 'ELECTRONICS AND COMPUTER' in branch_full_upper:
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

                # Keep the full course name with code for display
                course = course_full.strip()

                raw_data.append((reg_no, branch, course))

            except Exception as e:
                continue

        print(f"✅ Read {len(raw_data)} students from Excel")

        # Show statistics
        subject_counts = Counter([c for _, _, c in raw_data])
        print(f"\nTotal subjects: {len(subject_counts)}")
        print("Subject distribution:")
        for subject, count in subject_counts.most_common():
            print(f"  {subject}: {count}")

        return raw_data

    except Exception as e:
        print(f"❌ Error reading Excel: {e}")
        return []


# ===================== REBALANCING (Uses Rebalancer Module) =====================
def rebalance_rooms(rooms):
    """Try to balance rooms better using the Rebalancer module"""
    rebalanced_rooms, stats = rebalancer.rebalance(rooms, exam_type='open_elective')
    return rebalanced_rooms


# ===================== REPORTING =====================
def save_report_to_files(rooms, original_counts, slot_date_folder, slot_file_path, total_actual_students):
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
            'student_count': total_actual_students,  # This is what results.html uses
            'subject_counts': original_counts,
            'elective_type': 'open_elective'
        }

        # Create folder if it doesn't exist
        os.makedirs(slot_date_folder, exist_ok=True)

        # Save as text report
        report_path = os.path.join(slot_date_folder, 'seating_report.txt')
        with open(report_path, "w", encoding="utf-8") as f:
            active_rooms = {k: v for k, v in rooms.items() if sum(len(col) for col in v.values()) > 0}
            room_names = sorted(active_rooms.keys(), key=lambda x: int(x.replace("Room", "")))

            arrangement['summary']['total_rooms'] = len(active_rooms)
            room_totals = []

            # First, build the room structure
            for r_name in room_names:
                r_data = active_rooms[r_name]
                occ = sum(len(col) for col in r_data.values())
                subjs = {st['subj'] for col in r_data.values() for st in col}
                room_totals.append(occ)
                total_placed += occ

                arrangement['rooms'][r_name] = {
                    'total': occ,
                    'blocks': {},
                    'subjects': list(subjs)
                }

                # Map columns to display block names
                for blk_orig in BLOCK_ORDER:
                    blk_mapped = BLOCK_MAPPING[blk_orig]
                    students = [st['roll'] for st in r_data[blk_orig]]
                    arrangement['rooms'][r_name]['blocks'][blk_mapped] = {
                        'students': students,
                        'capacity': COL_CAPACITY[blk_orig],
                        'count': len(students)
                    }

            # Update summary with placed count
            arrangement['summary']['best_leftovers'] = total_actual_students - total_placed

            # Calculate statistics
            if room_totals:
                avg = sum(room_totals) / len(room_totals)
                min_val = min(room_totals)
                max_val = max(room_totals)
                diff = max_val - min_val

                arrangement['summary']['average'] = avg
                arrangement['summary']['min'] = min_val
                arrangement['summary']['max'] = max_val
                arrangement['summary']['actual_difference'] = diff
                arrangement['summary']['best_difference'] = diff

            # Now create QP summary by directly counting from rooms
            room_wise = []
            subject_summary = defaultdict(int)

            for r_name in room_names:
                r_data = active_rooms[r_name]
                # Count subjects in this room
                room_subjects = defaultdict(int)

                for blk_orig in BLOCK_ORDER:
                    for student in r_data[blk_orig]:
                        subject = student['full_subj']
                        room_subjects[subject] += 1
                        subject_summary[subject] += 1

                # Add to room_wise list
                for subject, count in room_subjects.items():
                    room_wise.append({
                        'Room': r_name,
                        'Subject': subject,
                        'Student Count': count
                    })

            # Sort room_wise
            room_wise.sort(key=lambda x: (int(re.search(r'\d+', x['Room']).group()), x['Subject']))

            # Add QP summary to arrangement
            arrangement['qp_summary'] = {
                'room_wise': room_wise,
                'subject_summary': dict(subject_summary),
                'total_students': total_placed
            }

            # Now write the text report
            for r_name in room_names:
                r_data = active_rooms[r_name]
                occ = sum(len(col) for col in r_data.values())
                subjs = {st['subj'] for col in r_data.values() for st in col}

                f.write(f"\n{'=' * 100}\n")
                f.write(f"{r_name.upper()} | TOTAL: {occ} | MAX: {ROOM_MAX_CAP} | SUBJECTS: {len(subjs)}\n")
                f.write(f"{'=' * 100}\n")

                # Column headers with display names
                display_headers = [BLOCK_MAPPING[b] for b in BLOCK_ORDER]
                f.write(f"{'Row':<4} | " + " | ".join([f"{h:<18}" for h in display_headers]) + "\n")
                f.write(f"{'-' * 100}\n")

                # Write rows
                max_rows = 7
                for i in range(max_rows):
                    row = f"{i + 1:<4} | "
                    for blk in BLOCK_ORDER:
                        if i < len(r_data[blk]):
                            st = r_data[blk][i]
                            cell = f"{st['roll']} ({st['branch']})"
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
                    full_subj_name = next((st['full_subj'] for col in r_data.values() for st in col if st['subj'] == s),
                                          s)
                    f.write(f"  {full_subj_name}: {c} students\n")

            f.write(f"\n{'=' * 100}\n")
            f.write(f"SUMMARY\n")
            f.write(f"{'=' * 100}\n")
            f.write(f"Total students in file: {total_actual_students}\n")
            f.write(f"Total students placed: {total_placed}\n")
            f.write(f"Leftover students: {total_actual_students - total_placed}\n")
            f.write(f"Rooms used: {len(active_rooms)}\n")

            if room_totals:
                f.write(f"Capacity difference: {diff} (Target: ≤6)\n")
                f.write(f"Average per room: {avg:.1f}\n")
                f.write(f"Min room: {min_val}, Max room: {max_val}\n")

            # Check for adjacent same subjects
            f.write(f"\n{'=' * 100}\n")
            f.write(f"ADJACENT SAME SUBJECT VERIFICATION\n")
            f.write(f"{'=' * 100}\n")

            violations = 0
            for r_name in room_names:
                r_data = active_rooms[r_name]
                for blk in BLOCK_ORDER:
                    col_idx = BLOCK_ORDER.index(blk)
                    if col_idx > 0:
                        left_blk = BLOCK_ORDER[col_idx - 1]
                        for st in r_data[blk]:
                            for left_st in r_data[left_blk]:
                                if st['subj'] == left_st['subj']:
                                    violations += 1
                                    f.write(
                                        f"⚠️ Violation in {r_name}: {st['subj']} appears in {BLOCK_MAPPING[blk]} and {BLOCK_MAPPING[left_blk]}\n")

            if violations == 0:
                f.write("✅ No adjacent same subjects found!\n")

        # Save JSON
        json_path = os.path.join(slot_date_folder, 'seating_arrangement.json')
        with open(json_path, 'w') as f:
            json.dump(arrangement, f, indent=2)

        # Save QP counts
        qp_path = os.path.join(slot_date_folder, 'qp_counts.txt')
        with open(qp_path, 'w', encoding='utf-8') as f:
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

            f.write(f"\nTOTAL STUDENTS PLACED: {arrangement['qp_summary']['total_students']}\n")
            f.write("=" * 60 + "\n")

        return arrangement

    except Exception as e:
        print(f"❌ Error saving reports: {e}")
        import traceback
        traceback.print_exc()
        return None


# ===================== MAIN ALGORITHM =====================
def generate_open_elective_arrangement(slot_file_path, slot_date_folder):
    """Main function for Flask - Open Elective"""
    try:
        print(f"\n{'=' * 80}")
        print(f"OPEN ELECTIVE ALGORITHM")
        print(f"Target room difference: ≤6")
        print(f"Rule: No same subject adjacent")
        print(f"{'=' * 80}\n")

        # 1. Read Excel file
        raw_data = read_excel_file(slot_file_path)
        if not raw_data:
            return {'error': 'No valid student data found', 'elective_type': 'open_elective'}

        total_actual_students = len(raw_data)
        print(f"\n✅ Successfully read {total_actual_students} students")

        # 2. Process data
        subj_map, elective_counts = extract_elective_counts(raw_data)
        total_students = sum(elective_counts.values())

        print(f"\n📊 Statistics:")
        print(f"  Total students: {total_students}")
        print(f"  Total subjects: {len(elective_counts)}")

        # Show all subjects
        print(f"\n📚 Subject distribution:")
        for subject, count in sorted(elective_counts.items(), key=lambda x: x[1], reverse=True):
            full_name = next((s[2] for s in raw_data if re.sub(r'\s*\([^)]*\)', '', s[2]).strip() == subject), subject)
            print(f"  {full_name}: {count}")

        # 3. Calculate room count
        min_rooms_needed = math.ceil(total_students / ROOM_MAX_CAP)
        base_rooms = max(min_rooms_needed, math.ceil(total_students / 30))

        print(f"\n🏢 Room calculation:")
        print(f"  Total students: {total_students}")
        print(f"  Max capacity per room: {ROOM_MAX_CAP}")
        print(f"  Minimum rooms needed: {min_rooms_needed}")
        print(f"  Base rooms: {base_rooms}")

        best_allocation = None
        best_diff = float('inf')
        best_left = float('inf')
        best_rooms_used = 0

        # 4. Try different room counts
        room_counts_to_try = list(range(max(min_rooms_needed, base_rooms - 2), min(20, base_rooms + 3)))
        print(f"  Trying room counts: {room_counts_to_try}")

        for rooms_try in room_counts_to_try:
            print(f"\n  Trying {rooms_try} rooms...")
            found_good_for_this_count = False

            for attempt in range(500):
                # Create a fresh deep copy for each attempt
                fresh_subj_map = copy.deepcopy(subj_map)

                final_rooms, left = generate_allocation(fresh_subj_map, elective_counts, rooms_try)

                # Calculate room totals for active rooms
                room_totals = []
                for room_data in final_rooms.values():
                    occ = sum(len(col) for col in room_data.values())
                    if occ > 0:
                        room_totals.append(occ)

                if not room_totals:
                    continue

                # Calculate difference
                diff = max(room_totals) - min(room_totals)

                # Track best solution
                if left == 0:
                    if diff < best_diff:
                        best_diff = diff
                        best_allocation = copy.deepcopy(final_rooms)
                        best_rooms_used = len(room_totals)
                        print(f"    ✓ Attempt {attempt + 1}: diff = {diff}, rooms used = {best_rooms_used}")

                        if diff <= 6:
                            print(f"      → Found good solution!")
                            found_good_for_this_count = True
                            break
                elif left < best_left:
                    best_left = left
                    print(f"    ⚠ Attempt {attempt + 1}: {left} students left, diff = {diff}")

                if (attempt + 1) % 100 == 0:
                    print(f"    ... {attempt + 1} attempts")

            if found_good_for_this_count:
                break

        # 5. If no zero-leftover solution found, take the one with fewest leftovers
        if best_allocation is None and best_left < float('inf'):
            print(f"\n⚠ No perfect solution found. Trying to allocate all students...")

            max_rooms = max(room_counts_to_try)
            for attempt in range(300):
                fresh_subj_map = copy.deepcopy(subj_map)
                final_rooms, left = generate_allocation(fresh_subj_map, elective_counts, max_rooms)

                if left == 0:
                    best_allocation = final_rooms
                    room_totals = [sum(len(col) for col in room_data.values()) for room_data in final_rooms.values() if
                                   sum(len(col) for col in room_data.values()) > 0]
                    best_diff = max(room_totals) - min(room_totals) if room_totals else 0
                    print(f"    ✓ Found allocation with {max_rooms} rooms")
                    break

        # 6. Final result
        if best_allocation:
            # Apply rebalancing if needed
            if best_diff > 6:
                best_allocation = rebalance_rooms(best_allocation)

                # Recalculate after rebalancing
                room_totals = []
                for room_data in best_allocation.values():
                    occ = sum(len(col) for col in room_data.values())
                    if occ > 0:
                        room_totals.append(occ)
                if room_totals:
                    best_diff = max(room_totals) - min(room_totals)

            # Calculate total placed students
            total_placed = 0
            for room_data in best_allocation.values():
                total_placed += sum(len(col) for col in room_data.values())

            rooms_used = len([r for r in best_allocation.values() if sum(len(col) for col in r.values()) > 0])

            print(f"\n{'=' * 80}")
            print(f"✅ SUCCESS!")
            print(f"  Total students in file: {total_actual_students}")
            print(f"  Students placed: {total_placed}")
            print(f"  Leftover: {total_actual_students - total_placed}")
            print(f"  Rooms used: {rooms_used}")
            print(f"  Capacity difference: {best_diff}")
            print(f"{'=' * 80}\n")

            # 7. Save results with actual total
            arrangement = save_report_to_files(best_allocation, elective_counts, slot_date_folder, slot_file_path,
                                               total_actual_students)

            if arrangement:
                return arrangement
            else:
                return {'error': 'Failed to save arrangement files', 'elective_type': 'open_elective'}
        else:
            print(f"\n❌ No valid allocation found")
            return {'error': 'No valid allocation found', 'elective_type': 'open_elective'}

    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'error': str(e), 'elective_type': 'open_elective'}


# ===================== TEST =====================
if __name__ == "__main__":
    # Simple test
    print("Testing open elective algorithm...")

    test_data = [
        ("LBT22CS045", "CSE", "PYTHON FOR EVERYONE (OEL352)"),
        ("LBT22CS067", "CSE", "PYTHON FOR EVERYONE (OEL352)"),
        ("LBT22EC008", "EC", "PYTHON FOR EVERYONE (OEL352)"),
        ("LBT22EC012", "EC", "PYTHON FOR EVERYONE (OEL352)"),
        ("LBT22IT009", "IT", "PYTHON FOR EVERYONE (OEL352)"),
        ("LBT22ME005", "ME", "PYTHON FOR EVERYONE (OEL352)"),
    ]

    print(f"Test with {len(test_data)} students")

    subj_map, elective_counts = extract_elective_counts(test_data)
    rooms_try = math.ceil(len(test_data) / 30)

    # Create a fresh copy for test
    fresh_subj_map = copy.deepcopy(subj_map)
    final_rooms, left = generate_allocation(fresh_subj_map, elective_counts, rooms_try)

    if left == 0:
        print(f"✅ Test successful!")
    else:
        print(f"❌ Test failed: {left} students left")