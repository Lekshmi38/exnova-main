import os
import re
import pandas as pd
import math
import random
from collections import defaultdict, Counter
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from werkzeug.utils import secure_filename
import json
from datetime import datetime

# Import new modules
from constraint_handler import ConstraintHandler
from rebalancer import Rebalancer

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize modules
constraint_handler = ConstraintHandler()
rebalancer = Rebalancer(constraint_handler)

# Import elective-specific algorithms
PROGRAM_ELECTIVE_AVAILABLE = False
OPEN_ELECTIVE_AVAILABLE = False

try:
    from program_elect import generate_program_elective_arrangement

    PROGRAM_ELECTIVE_AVAILABLE = True
    print("✓ Program elective algorithm loaded successfully")
except ImportError as e:
    print(f"Warning: program_elect.py not found or has errors: {e}")
    PROGRAM_ELECTIVE_AVAILABLE = False

try:
    from open_elect import generate_open_elective_arrangement

    OPEN_ELECTIVE_AVAILABLE = True
    print("✓ Open elective algorithm loaded successfully")
except ImportError as e:
    print(f"Warning: open_elect.py not found or has errors: {e}")
    OPEN_ELECTIVE_AVAILABLE = False


# =============================
# 1. UPLOAD & SORTING FUNCTIONS
# =============================

def normalize_columns(df):
    df.columns = (
        df.columns
        .astype(str)
        .str.replace('"', '', regex=False)
        .str.replace('\t', '', regex=False)
        .str.strip()
    )
    return df


def extract_student_info(df, student_col):
    def extract_name(student):
        match = re.match(r'(.+?)\(', str(student))
        return match.group(1).strip() if match else str(student)

    def extract_regno(student):
        match = re.search(r'\(([^)]+)\)', str(student))
        return match.group(1).strip() if match else ""

    df['Student Name'] = df[student_col].apply(extract_name)
    df['Register No'] = df[student_col].apply(extract_regno)
    return df


def extract_sorting_keys(df):
    def extract_year(reg):
        match = re.search(r'(?:LLBT|LBT)(\d{2})', str(reg))
        return int(match.group(1)) if match else 99

    def extract_serial(reg):
        match = re.search(r'([A-Z]{2})(\d{3})$', str(reg))
        return int(match.group(2)) if match else 999

    df['_year'] = df['Register No'].apply(extract_year)
    df['_serial'] = df['Register No'].apply(extract_serial)
    return df


def process_master_file(file_path, exam_type, semester, month_year, elective_type='general'):
    """Process master file and create sorted files with proper folder structure"""
    try:
        df = pd.read_excel(file_path)
        df = normalize_columns(df)

        # Detect required columns
        student_col = None
        for col in df.columns:
            if 'student' in col.lower() or 'name' in col.lower():
                student_col = col
                break

        if not student_col:
            raise ValueError("Could not find student column")

        # Extract other columns
        branch_col = next((col for col in df.columns if 'branch' in col.lower()), 'Branch Name')
        slot_col = next((col for col in df.columns if 'slot' in col.lower()), 'Slot')
        course_col = next((col for col in df.columns if 'course' in col.lower()), 'Course')
        exam_date_col = next((col for col in df.columns if 'exam' in col.lower() and 'date' in col.lower()),
                             'Exam Date')

        # Extract student info
        df = extract_student_info(df, student_col)
        df = extract_sorting_keys(df)

        # Build final output
        final_df = df[[
            'Student Name',
            'Register No',
            branch_col,
            slot_col,
            course_col,
            exam_date_col if exam_date_col in df.columns else 'Exam Date'
        ]].copy()

        final_df.columns = [
            'Student',
            'Register No',
            'Branch Name',
            'Slot',
            'Course',
            'Exam Date'
        ]

        # Add sorting keys
        final_df['_year'] = df['_year']
        final_df['_serial'] = df['_serial']

        # Sort data
        final_df = final_df.sort_values(
            by=['Branch Name', 'Slot', '_year', '_serial'],
            ascending=[True, True, True, True],
            kind='mergesort'
        )

        # Add serial numbers
        final_df.insert(0, 'Sl.No', range(1, len(final_df) + 1))
        final_df = final_df.drop(columns=['_year', '_serial'])

        # Create directory structure
        if elective_type != 'general':
            # For elective types: exam_type/elective_type/semester/month_year/
            base_path = os.path.join(
                app.config['UPLOAD_FOLDER'],
                str(exam_type),
                str(elective_type),
                str(semester),
                str(month_year)
            )
        else:
            # For general: exam_type/semester/month_year/
            base_path = os.path.join(
                app.config['UPLOAD_FOLDER'],
                str(exam_type),
                str(semester),
                str(month_year)
            )

        master_dir = os.path.join(base_path, 'master_list')
        os.makedirs(master_dir, exist_ok=True)

        # Save master file
        master_file_path = os.path.join(master_dir, 'Master_Sorted_List.xlsx')
        final_df.to_excel(master_file_path, index=False)

        # Create slot-wise folders
        slot_files = {}

        for slot in final_df['Slot'].dropna().unique():
            slot_str = str(slot).strip()
            # Create slot folder
            slot_folder = os.path.join(base_path, f'slot_{slot_str}')
            os.makedirs(slot_folder, exist_ok=True)

            # Get unique exam dates for this slot
            slot_dates = final_df[final_df['Slot'] == slot_str]['Exam Date'].unique()

            # Create sorted slot file for each date
            for exam_date in slot_dates:
                # Convert date to string format
                if pd.isna(exam_date):
                    date_str = 'NoDate'
                else:
                    try:
                        date_str = pd.to_datetime(exam_date).strftime('%Y-%m-%d')
                    except:
                        date_str = str(exam_date)

                # Create date folder inside slot folder
                date_folder = os.path.join(slot_folder, date_str)
                os.makedirs(date_folder, exist_ok=True)

                # Filter data for this slot and date
                slot_df = final_df[(final_df['Slot'] == slot_str) & (final_df['Exam Date'] == exam_date)].copy()
                if len(slot_df) > 0:
                    slot_df['Sl.No'] = range(1, len(slot_df) + 1)

                    slot_filename = f'Slot_{slot_str}_{date_str}_Sorted_List.xlsx'
                    slot_file_path = os.path.join(date_folder, slot_filename)
                    slot_df.to_excel(slot_file_path, index=False)

                    # Store file info
                    if slot_str not in slot_files:
                        slot_files[slot_str] = []
                    slot_files[slot_str].append({
                        'date': date_str,
                        'file_path': slot_file_path,
                        'folder': date_folder,
                        'student_count': len(slot_df)
                    })

        # Store elective type in result
        return {
            'success': True,
            'master_file': master_file_path,
            'slot_files': slot_files,
            'slots': list(final_df['Slot'].dropna().unique()),
            'elective_type': elective_type,
            'base_path': base_path
        }

    except Exception as e:
        print(f"Error in process_master_file: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'error': str(e)
        }


# =============================
# 2. SEATING ALLOCATION FUNCTIONS (UPDATED WITH CHM & RBM)
# =============================

# Constants (now from constraint_handler)
BLOCK_ORDER = constraint_handler.BLOCK_ORDER
BLOCK_CAPACITY = constraint_handler.BLOCK_CAPACITY
MAX_TOTAL_ROOM = constraint_handler.MAX_TOTAL_ROOM
BRANCH_MAP = constraint_handler.BRANCH_MAP
CLASS_GROUP = constraint_handler.CLASS_GROUP

TARGET_MAX_DIFFERENCE = 4
MAX_ATTEMPTS = 1000
MAX_PERFECT_ATTEMPTS = 100


def roll_key(r):
    """Wrapper for constraint_handler.roll_key"""
    return constraint_handler.roll_key(r)


def generate_allocation(classes, supply_data, session_subjects):
    pool = []
    for cls, count in classes.items():
        s_count = sum(supply_data.get(cls, {}).values())
        pool.extend([cls] * (count + s_count))

    random.shuffle(pool)
    current_pool = pool.copy()

    avg_per_room = 31
    num_rooms = math.ceil(len(pool) / avg_per_room)
    rooms = {}

    target_per_room = []
    total_students = len(pool)
    for i in range(num_rooms):
        if i == num_rooms - 1:
            target_per_room.append(total_students - sum(target_per_room))
        else:
            base = total_students // num_rooms
            variation = random.randint(-3, 3)
            target_per_room.append(min(base + variation, MAX_TOTAL_ROOM))

    for r in range(num_rooms):
        if not current_pool:
            break

        room = f"Room{r + 1}"
        room_target = target_per_room[r]

        counts = Counter(current_pool)
        if not counts:
            break

        main_sub = max(counts.items(), key=lambda x: x[1])[0]
        main_group = CLASS_GROUP.get(main_sub)

        possible_secondary = []
        for cls, cnt in counts.items():
            if cls == main_sub:
                continue
            if CLASS_GROUP.get(cls) != main_group:
                possible_secondary.append((cls, cnt))

        possible_secondary.sort(key=lambda x: x[1], reverse=True)

        if possible_secondary:
            sec_sub = possible_secondary[0][0]
        else:
            sec_sub = main_sub

        main_count = counts[main_sub]
        sec_count = counts.get(sec_sub, 0)

        target_main = min(main_count, max(10, int(room_target * 0.65)))
        target_sec = min(sec_count, room_target - target_main)

        remaining = room_target - (target_main + target_sec)
        while remaining > 0:
            if target_main < main_count and target_main < 24:
                target_main += 1
            elif target_sec < sec_count and target_sec < 16:
                target_sec += 1
            else:
                break
            remaining = room_target - (target_main + target_sec)

        rooms[room] = {
            "sub_a": {"cls": main_sub, "qty": target_main},
            "sub_b": {"cls": sec_sub, "qty": target_sec}
        }

        for _ in range(target_main):
            if main_sub in current_pool:
                current_pool.remove(main_sub)

        if main_sub != sec_sub:
            for _ in range(target_sec):
                if sec_sub in current_pool:
                    current_pool.remove(sec_sub)

    return rooms, current_pool


def create_block_layout(rooms_data, subjects):
    final = {}

    for room_name, data in rooms_data.items():
        blocks = {b: {} for b in BLOCK_ORDER}

        a_cls = data["sub_a"]["cls"]
        a_qty = data["sub_a"]["qty"]

        if a_qty > 0:
            total_to_distribute = a_qty
            left1_cap = BLOCK_CAPACITY["Left1"]
            middle2_cap = BLOCK_CAPACITY["Middle2"]
            right3_cap = BLOCK_CAPACITY["Right3"]

            if total_to_distribute > 0:
                middle_qty = min(total_to_distribute, middle2_cap)
                if middle_qty > 0:
                    blocks["Middle2"] = {"cls": a_cls, "qty": middle_qty, "subject": subjects.get(a_cls)}
                    total_to_distribute -= middle_qty

            if total_to_distribute > 0:
                if total_to_distribute <= (left1_cap + right3_cap):
                    left_qty = min(math.ceil(total_to_distribute / 2), left1_cap)
                    right_qty = total_to_distribute - left_qty

                    if right_qty > right3_cap:
                        right_qty = right3_cap
                        left_qty = total_to_distribute - right_qty

                    if left_qty > 0:
                        blocks["Left1"] = {"cls": a_cls, "qty": left_qty, "subject": subjects.get(a_cls)}
                    if right_qty > 0:
                        blocks["Right3"] = {"cls": a_cls, "qty": right_qty, "subject": subjects.get(a_cls)}
                else:
                    left_qty = min(total_to_distribute, left1_cap)
                    if left_qty > 0:
                        blocks["Left1"] = {"cls": a_cls, "qty": left_qty, "subject": subjects.get(a_cls)}
                        total_to_distribute -= left_qty

                    if total_to_distribute > 0:
                        right_qty = min(total_to_distribute, right3_cap)
                        if right_qty > 0:
                            blocks["Right3"] = {"cls": a_cls, "qty": right_qty, "subject": subjects.get(a_cls)}

        b_cls = data["sub_b"]["cls"]
        b_qty = data["sub_b"]["qty"]

        if b_qty > 0 and b_cls != a_cls:
            total_to_distribute = b_qty
            left3_cap = BLOCK_CAPACITY["Left3"]
            right1_cap = BLOCK_CAPACITY["Right1"]

            if total_to_distribute <= (left3_cap + right1_cap):
                left3_qty = min(math.ceil(total_to_distribute / 2), left3_cap)
                right1_qty = total_to_distribute - left3_qty

                if right1_qty > right1_cap:
                    right1_qty = right1_cap
                    left3_qty = total_to_distribute - right1_qty

                if left3_qty > 0:
                    blocks["Left3"] = {"cls": b_cls, "qty": left3_qty, "subject": subjects.get(b_cls)}
                if right1_qty > 0:
                    blocks["Right1"] = {"cls": b_cls, "qty": right1_qty, "subject": subjects.get(b_cls)}
            else:
                left3_qty = min(total_to_distribute, left3_cap)
                if left3_qty > 0:
                    blocks["Left3"] = {"cls": b_cls, "qty": left3_qty, "subject": subjects.get(b_cls)}
                    total_to_distribute -= left3_qty

                if total_to_distribute > 0:
                    right1_qty = min(total_to_distribute, right1_cap)
                    if right1_qty > 0:
                        blocks["Right1"] = {"cls": b_cls, "qty": right1_qty, "subject": subjects.get(b_cls)}

        final[room_name] = blocks

    return final


def cleanup_leftovers(rooms, leftovers, subjects):
    if not leftovers:
        return rooms

    counts = Counter(leftovers)

    for cls, count in counts.items():
        room_order = sorted(rooms.keys(),
                            key=lambda x: sum(b.get("qty", 0) for b in rooms[x].values()))

        for room_name in room_order:
            if count <= 0:
                break

            blocks = rooms[room_name]
            current_total = sum(b.get("qty", 0) for b in blocks.values())

            if current_total >= MAX_TOTAL_ROOM:
                continue

            existing_blocks = []
            for block_name, block_data in blocks.items():
                if block_data and block_data.get("cls") == cls:
                    existing_blocks.append((block_name, block_data))

            if existing_blocks:
                for block_name, block_data in existing_blocks:
                    if count <= 0:
                        break
                    if block_data.get("qty", 0) < BLOCK_CAPACITY[block_name]:
                        add_qty = min(count, BLOCK_CAPACITY[block_name] - block_data["qty"])
                        block_data["qty"] += add_qty
                        count -= add_qty
            else:
                for block_name in BLOCK_ORDER:
                    if count <= 0:
                        break
                    if not blocks[block_name]:
                        subjects_in_room = len({b["cls"] for b in blocks.values() if b})
                        if subjects_in_room < 2:
                            add_qty = min(count, BLOCK_CAPACITY[block_name], MAX_TOTAL_ROOM - current_total)
                            blocks[block_name] = {
                                "cls": cls,
                                "qty": add_qty,
                                "subject": subjects.get(cls)
                            }
                            count -= add_qty
                            break

    return rooms


def calculate_room_difference(rooms_data):
    room_totals = []
    for room, blocks in rooms_data.items():
        total = sum(b.get("qty", 0) for b in blocks.values())
        room_totals.append(total)

    if not room_totals:
        return float('inf')

    return max(room_totals) - min(room_totals)


def generate_qp_counts(arrangement, slot_file_path):
    """Generate accurate QP counts by analyzing actual student data"""
    try:
        # Read the original slot file to get branch-subject mapping
        df = pd.read_excel(slot_file_path)

        # Create branch-subject mapping from the original data
        branch_subject_map = {}
        for _, row in df.iterrows():
            reg_no = str(row.get("Register No", "")).strip()
            branch = str(row.get("Branch Name", "")).strip()
            course = str(row.get("Course", "")).strip()

            if branch and course:
                bcode = BRANCH_MAP.get(branch)
                if bcode and bcode not in branch_subject_map:
                    branch_subject_map[bcode] = course

        # Also use the branch_subject_map from arrangement
        if 'branch_subject_map' in arrangement:
            for bcode, subject in arrangement['branch_subject_map'].items():
                if bcode not in branch_subject_map:
                    branch_subject_map[bcode] = subject

        # Debug: Print the mapping
        print(f"Branch-Subject Mapping: {branch_subject_map}")

        # Get student roll numbers from each room's blocks
        room_student_map = {}
        for room_name, room_data in arrangement['rooms'].items():
            room_students = []
            for block_name, block_data in room_data['blocks'].items():
                room_students.extend(block_data['students'])
            room_student_map[room_name] = room_students

        # Calculate QP counts
        qp_data = []
        subject_totals = {}

        # Define branch patterns for matching roll numbers
        branch_patterns = {
            'S7CSE': ['CS', 'CSE'],
            'S7IT': ['IT'],
            'S7EC': ['EC'],
            'S7ER': ['ECE', 'ER'],
            'S7CE': ['CE']
        }

        for room_name, students in room_student_map.items():
            # Count subjects in this room
            room_subjects = {}

            for student_roll in students:
                if student_roll == '--' or not student_roll:
                    continue

                student_roll_upper = student_roll.upper()
                matched_branch = None

                # Try to match branch code from roll number
                for bcode, patterns in branch_patterns.items():
                    for pattern in patterns:
                        if pattern in student_roll_upper:
                            matched_branch = bcode
                            break
                    if matched_branch:
                        break

                # If still not matched, try other patterns
                if not matched_branch:
                    if 'LLBT' in student_roll_upper or 'LBT' in student_roll_upper:
                        # Extract 2-letter code after year
                        match = re.search(r'(?:LLBT|LBT)\d{2}([A-Z]{2,3})', student_roll_upper)
                        if match:
                            dept_code = match.group(1)
                            if dept_code in ['CS', 'CSE']:
                                matched_branch = 'S7CSE'
                            elif dept_code == 'IT':
                                matched_branch = 'S7IT'
                            elif dept_code == 'EC':
                                matched_branch = 'S7EC'
                            elif dept_code in ['ECE', 'ER']:
                                matched_branch = 'S7ER'
                            elif dept_code == 'CE':
                                matched_branch = 'S7CE'

                if matched_branch and matched_branch in branch_subject_map:
                    subject_name = branch_subject_map[matched_branch]

                    # Add to room subjects
                    if subject_name not in room_subjects:
                        room_subjects[subject_name] = 0
                    room_subjects[subject_name] += 1

                    # Update overall totals
                    if subject_name not in subject_totals:
                        subject_totals[subject_name] = 0
                    subject_totals[subject_name] += 1

            # Add room-wise counts
            for subject_name, count in room_subjects.items():
                qp_data.append({
                    'Room': room_name,
                    'Subject': subject_name,
                    'Student Count': count
                })

        # Sort QP data by room and subject
        qp_data.sort(key=lambda x: (int(re.search(r'\d+', x['Room']).group()), x['Subject']))

        # Create summary
        qp_summary = {
            'room_wise': qp_data,
            'subject_summary': subject_totals,
            'total_students': sum(subject_totals.values()) if subject_totals else 0
        }

        print(f"QP Summary generated: {qp_summary}")
        return qp_summary

    except Exception as e:
        print(f"Error generating QP counts: {e}")
        import traceback
        traceback.print_exc()
        return None

def generate_seating_arrangement(slot_file_path, slot_date_folder, elective_type='general'):
    """Generate seating arrangement based on elective type"""
    try:
        print(f"\n{'=' * 60}")
        print(f"Generating seating arrangement for: {slot_file_path}")
        print(f"Elective Type: {elective_type}")
        print(f"Date Folder: {slot_date_folder}")
        print(f"{'=' * 60}\n")

        if elective_type == 'program_elective' and PROGRAM_ELECTIVE_AVAILABLE:
            print("Using program elective algorithm")
            arrangement = generate_program_elective_arrangement(slot_file_path, slot_date_folder)
        elif elective_type == 'open_elective' and OPEN_ELECTIVE_AVAILABLE:
            print("Using open elective algorithm")
            arrangement = generate_open_elective_arrangement(slot_file_path, slot_date_folder)
        else:
            print("Using general algorithm with CHM & RBM")

            # Use constraint handler to process student data
            df = pd.read_excel(slot_file_path)
            df.columns = df.columns.str.strip()

            # Process data using CHM
            student_data = constraint_handler.process_student_data(df, elective_type)

            MASTER_ROLLS = student_data['rolls']
            MASTER_SUBJECTS = student_data['subjects']
            classes_count = student_data['classes_count']
            supply_data = student_data['supply_data']
            total_students = student_data['total_students']

            # PHASE 1: Find solution with 0 leftovers
            best_solution_phase1 = None
            best_leftovers_phase1 = float('inf')
            best_difference_phase1 = float('inf')
            phase1_completed = False

            for attempt in range(MAX_ATTEMPTS):
                rooms_data, leftovers = generate_allocation(classes_count, supply_data, MASTER_SUBJECTS)
                block_layout = create_block_layout(rooms_data, MASTER_SUBJECTS)

                # Apply rebalancing
                block_layout, rebalance_stats = rebalancer.rebalance(block_layout, elective_type)
                block_layout = rebalancer.cleanup_empty_blocks(block_layout)

                block_layout = cleanup_leftovers(block_layout, leftovers, MASTER_SUBJECTS)

                total_students = sum(len(rolls) for rolls in MASTER_ROLLS.values())
                allocated_students = sum(
                    sum(b.get("qty", 0) for b in room.values())
                    for room in block_layout.values()
                )
                current_leftovers = total_students - allocated_students
                current_difference = calculate_room_difference(block_layout)

                if current_leftovers < best_leftovers_phase1 or (
                        current_leftovers == best_leftovers_phase1 and current_difference < best_difference_phase1):
                    best_leftovers_phase1 = current_leftovers
                    best_difference_phase1 = current_difference
                    best_solution_phase1 = block_layout.copy()

                if current_leftovers == 0:
                    phase1_completed = True
                    break

            if not phase1_completed:
                best_solution = best_solution_phase1
                best_leftovers = best_leftovers_phase1
                best_difference = best_difference_phase1
            else:
                # PHASE 2: Find better balanced solution
                best_solution = best_solution_phase1
                best_leftovers = 0
                best_difference = best_difference_phase1

                for attempt in range(MAX_PERFECT_ATTEMPTS):
                    rooms_data, leftovers = generate_allocation(classes_count, supply_data, MASTER_SUBJECTS)
                    block_layout = create_block_layout(rooms_data, MASTER_SUBJECTS)

                    # Apply rebalancing
                    block_layout, rebalance_stats = rebalancer.rebalance(block_layout, elective_type)
                    block_layout = rebalancer.cleanup_empty_blocks(block_layout)

                    block_layout = cleanup_leftovers(block_layout, leftovers, MASTER_SUBJECTS)

                    total_students = sum(len(rolls) for rolls in MASTER_ROLLS.values())
                    allocated_students = sum(
                        sum(b.get("qty", 0) for b in room.values())
                        for room in block_layout.values()
                    )
                    current_leftovers = total_students - allocated_students
                    current_difference = calculate_room_difference(block_layout)

                    if current_leftovers == 0:
                        if current_difference < best_difference:
                            best_difference = current_difference
                            best_solution = block_layout.copy()

                        if current_difference <= TARGET_MAX_DIFFERENCE:
                            break

            rooms_data = best_solution
            working_rolls = {k: list(v) for k, v in MASTER_ROLLS.items()}

            # Prepare final seating arrangement
            arrangement = {
                'rooms': {},
                'summary': {
                    'total_rooms': len(rooms_data),
                    'best_leftovers': best_leftovers,
                    'best_difference': best_difference,
                    'target_difference': TARGET_MAX_DIFFERENCE
                },
                'student_count': sum(len(rolls) for rolls in MASTER_ROLLS.values()),
                'branch_subject_map': MASTER_SUBJECTS,
                'elective_type': elective_type
            }

            for r in sorted(rooms_data.keys(), key=lambda x: int(re.search(r'\d+', x).group())):
                blocks = rooms_data[r]

                # Prepare column data
                col_data = {b: [] for b in BLOCK_ORDER}
                for blk in BLOCK_ORDER:
                    b = blocks.get(blk)
                    if b and b.get("cls"):
                        for _ in range(b["qty"]):
                            if working_rolls[b["cls"]]:
                                col_data[blk].append(working_rolls[b["cls"]].pop(0))

                # Calculate room total
                room_total = sum(len(v) for v in col_data.values())

                # Get subjects
                subjects_set = set()
                for b in blocks.values():
                    if b and b.get('cls'):
                        subjects_set.add(f"{b['cls']}: {b['subject']}")

                arrangement['rooms'][r] = {
                    'total': room_total,
                    'blocks': {},
                    'subjects': list(subjects_set)
                }

                for blk in BLOCK_ORDER:
                    arrangement['rooms'][r]['blocks'][blk] = {
                        'students': col_data[blk],
                        'capacity': BLOCK_CAPACITY[blk],
                        'count': len(col_data[blk])
                    }

            # Calculate statistics
            room_totals = []
            for r in arrangement['rooms']:
                room_totals.append(arrangement['rooms'][r]['total'])

            if room_totals:
                arrangement['summary']['average'] = sum(room_totals) / len(room_totals)
                arrangement['summary']['min'] = min(room_totals)
                arrangement['summary']['max'] = max(room_totals)
                arrangement['summary']['actual_difference'] = max(room_totals) - min(room_totals)

        # Generate QP counts - only for general/normal exams
        # Elective exams (program_elective and open_elective) already have their own QP summary
        if elective_type == 'general':
            qp_summary = generate_qp_counts(arrangement, slot_file_path)
            if qp_summary:
                arrangement['qp_summary'] = qp_summary
        # For elective types, ensure qp_summary exists
        elif 'qp_summary' not in arrangement:
            # Fallback - shouldn't happen as elective modules should provide it
            arrangement['qp_summary'] = {
                'room_wise': [],
                'subject_summary': {},
                'total_students': arrangement.get('student_count', 0)
            }

        # Save arrangement files to slot folder
        save_arrangement_files(arrangement, slot_date_folder, slot_file_path)

        return arrangement

    except Exception as e:
        print(f"Error in generate_seating_arrangement: {e}")
        import traceback
        traceback.print_exc()
        return {'error': str(e), 'elective_type': elective_type}
def save_arrangement_files(arrangement, slot_date_folder, slot_file_path):
    try:
        # Save arrangement as JSON
        arrangement_json_path = os.path.join(slot_date_folder, 'seating_arrangement.json')
        with open(arrangement_json_path, 'w') as f:
            json.dump(arrangement, f, indent=2)

        # Save as text report (for printing)
        report_path = os.path.join(slot_date_folder, 'seating_report.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("AUTOMATED SEATING ARRANGEMENT REPORT\n")
            if 'elective_type' in arrangement:
                f.write(f"Elective Type: {arrangement['elective_type'].replace('_', ' ').title()}\n")
            f.write("=" * 80 + "\n\n")

            # Add QP counts summary at top
            if 'qp_summary' in arrangement:
                f.write("QUESTION PAPER COUNT SUMMARY\n")
                f.write("-" * 40 + "\n")
                for subject, count in arrangement['qp_summary']['subject_summary'].items():
                    f.write(f"{subject}: {count} students\n")
                f.write(f"Total Students: {arrangement['qp_summary']['total_students']}\n")
                f.write("\n" + "=" * 80 + "\n\n")

            for room_name, room_data in arrangement['rooms'].items():
                f.write("=" * 80 + "\n")
                f.write(f" {room_name} | TOTAL: {room_data['total']} | BLOCK CAPACITY: 7-7-6-7-7 \n")
                f.write("=" * 80 + "\n")
                f.write(
                    "Row   | Left1 (7)       | Left3 (7)       | Middle2 (6)     | Right1 (7)      | Right3 (7)     \n")
                f.write("-" * 95 + "\n")

                for row in range(1, 8):
                    left1 = room_data['blocks']['Left1']['students'][row - 1] if row <= len(
                        room_data['blocks']['Left1']['students']) else '--'
                    left3 = room_data['blocks']['Left3']['students'][row - 1] if row <= len(
                        room_data['blocks']['Left3']['students']) else '--'
                    middle2 = room_data['blocks']['Middle2']['students'][row - 1] if row <= 6 and row <= len(
                        room_data['blocks']['Middle2']['students']) else '--'
                    right1 = room_data['blocks']['Right1']['students'][row - 1] if row <= len(
                        room_data['blocks']['Right1']['students']) else '--'
                    right3 = room_data['blocks']['Right3']['students'][row - 1] if row <= len(
                        room_data['blocks']['Right3']['students']) else '--'

                    left1 = left1.ljust(15)
                    left3 = left3.ljust(15)
                    middle2 = middle2.ljust(15)
                    right1 = right1.ljust(15)
                    right3 = right3.ljust(15)

                    if row == 7:
                        f.write(f"{row:<5} | {left1} | {left3} | {' '.ljust(15)} | {right1} | {right3} |\n")
                    else:
                        f.write(f"{row:<5} | {left1} | {left3} | {middle2} | {right1} | {right3} |\n")

                f.write("-" * 95 + "\n")
                f.write(
                    f"Block Usage: Left1: {room_data['blocks']['Left1']['count']}/7, Left3: {room_data['blocks']['Left3']['count']}/7, Middle2: {room_data['blocks']['Middle2']['count']}/6, Right1: {room_data['blocks']['Right1']['count']}/7, Right3: {room_data['blocks']['Right3']['count']}/7\n")
                f.write(f"Subjects: {', '.join(room_data['subjects'])}\n\n")

            # Save summary
            f.write("=" * 80 + "\n")
            f.write("SUMMARY\n")
            f.write("=" * 80 + "\n")
            f.write(f"Total Rooms: {arrangement['summary']['total_rooms']}\n")
            f.write(f"Total Students: {arrangement['student_count']}\n")
            f.write(f"Average per Room: {arrangement['summary']['average']:.1f}\n")
            f.write(f"Leftover Students: {arrangement['summary']['best_leftovers']}\n")
            f.write(f"Max Room Difference: {arrangement['summary']['actual_difference']}\n")

        # Save QP counts as separate readable file
        if 'qp_summary' in arrangement:
            qp_path = os.path.join(slot_date_folder, 'qp_counts.txt')
            with open(qp_path, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write("QUESTION PAPER COUNT REPORT\n")
                if 'elective_type' in arrangement:
                    f.write(f"Elective Type: {arrangement['elective_type'].replace('_', ' ').title()}\n")
                f.write("=" * 60 + "\n\n")

                f.write("ROOM-WISE DISTRIBUTION\n")
                f.write("-" * 60 + "\n")
                for item in arrangement['qp_summary']['room_wise']:
                    f.write(f"{item['Room']}: {item['Subject']} - {item['Student Count']} students\n")

                f.write("\n" + "=" * 60 + "\n")
                f.write("SUBJECT SUMMARY\n")
                f.write("=" * 60 + "\n")
                for subject, count in sorted(arrangement['qp_summary']['subject_summary'].items()):
                    f.write(f"{subject}: {count} students\n")

                f.write(f"\nTotal Students: {arrangement['qp_summary']['total_students']}\n")
                f.write("=" * 60 + "\n")

        return True
    except Exception as e:
        print(f"Error saving arrangement files: {e}")
        return False


# =============================
# 3. FLASK ROUTES (UNCHANGED)
# =============================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/set_exam_type', methods=['POST'])
def set_exam_type():
    exam_type = request.form.get('exam_type')
    if exam_type:
        session['exam_type'] = exam_type
        flash(f'Examination type set to: {exam_type}', 'success')
    return redirect(url_for('index'))


@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        if 'exam_type' not in session:
            flash('Please set examination type first from Home page', 'error')
            return redirect(url_for('upload'))

        semester = request.form.get('semester')
        month_year = request.form.get('month_year')
        file = request.files.get('master_file')
        elective_type = request.form.get('elective_type', 'general')

        if not all([semester, month_year, file]):
            flash('Please fill all fields and select a file', 'error')
            return redirect(url_for('upload'))

        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('upload'))

        # Check if elective-specific algorithm is available
        if elective_type == 'program_elective' and not PROGRAM_ELECTIVE_AVAILABLE:
            flash('Program elective algorithm not available. Please install program_elect.py', 'warning')
            elective_type = 'general'
        elif elective_type == 'open_elective' and not OPEN_ELECTIVE_AVAILABLE:
            flash('Open elective algorithm not available. Please install open_elect.py', 'warning')
            elective_type = 'general'

        # Save uploaded file temporarily
        filename = secure_filename(file.filename)
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp', filename)
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        file.save(temp_path)

        # Process the file with elective type
        result = process_master_file(temp_path, session['exam_type'], semester, month_year, elective_type)

        if result['success']:
            flash(
                f'File uploaded and processed successfully! (Elective Type: {elective_type.replace("_", " ").title()})',
                'success')
            session['last_upload'] = {
                'semester': semester,
                'month_year': month_year,
                'slots': result['slots'],
                'elective_type': elective_type
            }
        else:
            flash(f'Error processing file: {result["error"]}', 'error')

        # Clean up temp file
        try:
            os.remove(temp_path)
        except:
            pass

        return redirect(url_for('upload'))

    return render_template('upload.html',
                           exam_type=session.get('exam_type'),
                           program_elective_available=PROGRAM_ELECTIVE_AVAILABLE,
                           open_elective_available=OPEN_ELECTIVE_AVAILABLE)


@app.route('/allocation', methods=['GET', 'POST'])
def allocation():
    if request.method == 'POST':
        semester = request.form.get('semester')
        month_year = request.form.get('month_year')
        slot = request.form.get('slot')
        elective_type = request.form.get('elective_type', 'general')

        if not all([semester, month_year, slot]):
            flash('Please fill all fields', 'error')
            return redirect(url_for('allocation'))

        if 'exam_type' not in session:
            flash('Please set examination type first from Home page', 'error')
            return redirect(url_for('allocation'))

        # Check if elective-specific algorithm is available
        if elective_type == 'program_elective' and not PROGRAM_ELECTIVE_AVAILABLE:
            flash('Program elective algorithm not available. Using general algorithm.', 'warning')
            elective_type = 'general'
        elif elective_type == 'open_elective' and not OPEN_ELECTIVE_AVAILABLE:
            flash('Open elective algorithm not available. Using general algorithm.', 'warning')
            elective_type = 'general'

        # Try multiple path combinations to find the slot folder
        possible_paths = []

        # 1. Path with elective_type (for program/open elective)
        if elective_type != 'general':
            path_with_elective = os.path.join(
                app.config['UPLOAD_FOLDER'],
                session['exam_type'],
                elective_type,
                semester,
                month_year,
                f'slot_{slot}'
            )
            possible_paths.append(path_with_elective)

        # 2. Path without elective_type (for general)
        path_without_elective = os.path.join(
            app.config['UPLOAD_FOLDER'],
            session['exam_type'],
            semester,
            month_year,
            f'slot_{slot}'
        )
        possible_paths.append(path_without_elective)

        # Find the existing path
        slot_folder = None
        for path in possible_paths:
            if os.path.exists(path):
                slot_folder = path
                print(f"Found slot folder: {slot_folder}")
                break

        if not slot_folder:
            flash(f'Slot folder not found for semester: {semester}, month-year: {month_year}, slot: {slot}', 'error')
            print(f"Checked paths: {possible_paths}")
            return redirect(url_for('allocation'))

        # Get date subfolders
        date_folders = []
        if os.path.exists(slot_folder):
            for item in os.listdir(slot_folder):
                item_path = os.path.join(slot_folder, item)
                if os.path.isdir(item_path):
                    date_folders.append(item)

        if not date_folders:
            flash(f'No date folders found in slot {slot}', 'error')
            return redirect(url_for('allocation'))

        # Use the first date folder (or let user choose)
        exam_date = date_folders[0]
        date_folder = os.path.join(slot_folder, exam_date)

        # Find slot file
        slot_files = [f for f in os.listdir(date_folder) if f.endswith('.xlsx') and f.startswith(f'Slot_{slot}_')]
        if not slot_files:
            flash(f'Slot file not found for date: {exam_date}, slot: {slot}', 'error')
            return redirect(url_for('allocation'))

        slot_file_path = os.path.join(date_folder, slot_files[0])
        print(f"Using slot file: {slot_file_path}")

        # Generate seating arrangement with elective type
        arrangement = generate_seating_arrangement(slot_file_path, date_folder, elective_type)

        if 'error' in arrangement:
            flash(f'Error generating arrangement: {arrangement["error"]}', 'error')
            return redirect(url_for('allocation'))

        # Save arrangement to session for display
        session['current_arrangement'] = arrangement
        session['arrangement_params'] = {
            'semester': semester,
            'month_year': month_year,
            'slot': slot,
            'exam_date': exam_date,
            'folder': date_folder,
            'elective_type': elective_type,
            'slot_file_path': slot_file_path
        }

        # Ensure arrangement summary has all required fields
        if 'summary' not in arrangement:
            arrangement['summary'] = {
                'total_rooms': len(arrangement.get('rooms', {})),
                'best_leftovers': 0,
                'best_difference': 0,
                'target_difference': TARGET_MAX_DIFFERENCE,
                'actual_difference': 0,
                'average': 0,
                'min': 0,
                'max': 0
            }

        # Calculate statistics if not already present
        if 'rooms' in arrangement and arrangement['rooms']:
            room_totals = []
            for room_data in arrangement['rooms'].values():
                room_totals.append(room_data['total'])

            if room_totals:
                arrangement['summary']['average'] = sum(room_totals) / len(room_totals)
                arrangement['summary']['min'] = min(room_totals)
                arrangement['summary']['max'] = max(room_totals)
                arrangement['summary']['actual_difference'] = max(room_totals) - min(room_totals)

        flash(
            f'Seating arrangement generated for Slot {slot} ({exam_date})! (Elective Type: {elective_type.replace("_", " ").title()})',
            'success')
        return render_template('results.html',
                               arrangement=arrangement,
                               params=session['arrangement_params'])

    return render_template('allocation.html',
                           exam_type=session.get('exam_type'),
                           program_elective_available=PROGRAM_ELECTIVE_AVAILABLE,
                           open_elective_available=OPEN_ELECTIVE_AVAILABLE)


@app.route('/preview', methods=['GET', 'POST'])
def preview():
    if request.method == 'POST':
        semester = request.form.get('semester')
        month_year = request.form.get('month_year')
        elective_type = request.form.get('elective_type', 'general')

        if not all([semester, month_year]):
            flash('Please fill all fields', 'error')
            return redirect(url_for('preview'))

        if 'exam_type' not in session:
            flash('Please set examination type first from Home page', 'error')
            return redirect(url_for('preview'))

        # Try multiple paths
        possible_paths = []

        # Path with elective_type
        if elective_type != 'general':
            path_with_elective = os.path.join(
                app.config['UPLOAD_FOLDER'],
                session['exam_type'],
                elective_type,
                semester,
                month_year
            )
            possible_paths.append(path_with_elective)

        # Path without elective_type
        path_without_elective = os.path.join(
            app.config['UPLOAD_FOLDER'],
            session['exam_type'],
            semester,
            month_year
        )
        possible_paths.append(path_without_elective)

        # Find existing path
        base_path = None
        for path in possible_paths:
            if os.path.exists(path):
                base_path = path
                break

        if not base_path:
            flash('No files found for the selected semester and month-year', 'error')
            return redirect(url_for('preview'))

        master_file = os.path.join(base_path, 'master_list', 'Master_Sorted_List.xlsx')

        # Get list of slot folders
        slot_folders = []
        if os.path.exists(base_path):
            for item in os.listdir(base_path):
                if item.startswith('slot_') and os.path.isdir(os.path.join(base_path, item)):
                    slot = item.replace('slot_', '')
                    slot_folder = os.path.join(base_path, item)

                    # Get date folders and their files
                    date_folders = []
                    if os.path.exists(slot_folder):
                        for date_item in os.listdir(slot_folder):
                            date_path = os.path.join(slot_folder, date_item)
                            if os.path.isdir(date_path):
                                files = []
                                qp_data = {}

                                # List all files
                                for file in os.listdir(date_path):
                                    if file.endswith('.xlsx'):
                                        files.append({'name': file, 'type': 'slot'})
                                    elif file.endswith('.txt') and 'qp_counts' in file:
                                        # Read QP counts
                                        qp_path = os.path.join(date_path, file)
                                        try:
                                            with open(qp_path, 'r') as f:
                                                qp_content = f.read()
                                                qp_data = {'content': qp_content, 'has_data': True}
                                        except:
                                            qp_data = {'has_data': False}
                                    elif file.endswith('.txt'):
                                        files.append({'name': file, 'type': 'report'})
                                    elif file.endswith('.json'):
                                        files.append({'name': file, 'type': 'json'})

                                date_folders.append({
                                    'date': date_item,
                                    'path': date_path,
                                    'files': files,
                                    'has_arrangement': any(f['type'] == 'report' for f in files),
                                    'qp_data': qp_data
                                })

                    slot_folders.append({
                        'name': slot,
                        'folder': slot_folder,
                        'date_folders': date_folders
                    })

        return render_template('preview.html',
                               semester=semester,
                               month_year=month_year,
                               elective_type=elective_type,
                               master_file_exists=os.path.exists(master_file),
                               slot_folders=slot_folders,
                               exam_type=session.get('exam_type'))

    return render_template('preview.html', exam_type=session.get('exam_type'))


@app.route('/download_master/<exam_type>/<semester>/<month_year>')
def download_master(exam_type, semester, month_year):
    # Try multiple paths
    possible_paths = [
        # General path
        os.path.join(
            app.config['UPLOAD_FOLDER'],
            exam_type,
            semester,
            month_year,
            'master_list',
            'Master_Sorted_List.xlsx'
        ),
        # Program elective path
        os.path.join(
            app.config['UPLOAD_FOLDER'],
            exam_type,
            'program_elective',
            semester,
            month_year,
            'master_list',
            'Master_Sorted_List.xlsx'
        ),
        # Open elective path
        os.path.join(
            app.config['UPLOAD_FOLDER'],
            exam_type,
            'open_elective',
            semester,
            month_year,
            'master_list',
            'Master_Sorted_List.xlsx'
        )
    ]

    for filepath in possible_paths:
        if os.path.exists(filepath):
            return send_file(filepath, as_attachment=True)

    flash('Master file not found', 'error')
    return redirect(url_for('preview'))


@app.route('/download_file/<exam_type>/<semester>/<month_year>/<slot>/<date>/<filename>')
def download_file(exam_type, semester, month_year, slot, date, filename):
    # Try multiple path combinations
    possible_paths = [
        # General path
        os.path.join(
            app.config['UPLOAD_FOLDER'],
            exam_type,
            semester,
            month_year,
            f'slot_{slot}',
            date,
            filename
        ),
        # Program elective path
        os.path.join(
            app.config['UPLOAD_FOLDER'],
            exam_type,
            'program_elective',
            semester,
            month_year,
            f'slot_{slot}',
            date,
            filename
        ),
        # Open elective path
        os.path.join(
            app.config['UPLOAD_FOLDER'],
            exam_type,
            'open_elective',
            semester,
            month_year,
            f'slot_{slot}',
            date,
            filename
        )
    ]

    for filepath in possible_paths:
        if os.path.exists(filepath):
            return send_file(filepath, as_attachment=True)

    flash('File not found', 'error')
    return redirect(url_for('preview'))


@app.route('/print_qp_counts')
def print_qp_counts():
    """Generate printable QP counts page"""
    if 'arrangement_params' not in session or 'current_arrangement' not in session:
        flash('No arrangement data found', 'error')
        return redirect(url_for('allocation'))

    arrangement = session['current_arrangement']
    params = session['arrangement_params']

    # Add current time for the report
    from datetime import datetime
    current_time = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    return render_template('print_qp.html',
                           arrangement=arrangement,
                           params=params,
                           session=session,
                           current_time=current_time)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
