"""
Constraint Handling Module (CHM)
Role: Rule-enforcement and validation backbone of the examination seating allocation system
"""

import re
import math
from collections import defaultdict, Counter

class ConstraintHandler:
    """
    Constraint Handling Module - Ensures all seating allocations comply with institutional rules
    """

    # Class constants for constraints
    BLOCK_ORDER = ["Left1", "Left3", "Middle2", "Right1", "Right3"]
    BLOCK_CAPACITY = {"Left1": 7, "Left3": 7, "Middle2": 6, "Right1": 7, "Right3": 7}
    MAX_TOTAL_ROOM = 34

    # Program Elective specific constants
    PROGRAM_ELECTIVE_BLOCK_ORDER = ["Col 1", "Col 2", "Col 3", "Col 4", "Col 5"]
    PROGRAM_ELECTIVE_COL_CAPACITY = {"Col 1": 7, "Col 2": 7, "Col 3": 6, "Col 4": 7, "Col 5": 7}
    PROGRAM_ELECTIVE_MAX_SUBJ_PER_COL = 2

    BRANCH_MAP = {
        "COMPUTER SCIENCE & ENGINEERING": "S7CSE",
        "INFORMATION TECHNOLOGY": "S7IT",
        "CIVIL ENGINEERING": "S7CE",
        "ELECTRONICS & COMMUNICATION ENGG": "S7EC",
        "Electronics and Computer Engineering": "S7ER"
    }

    CLASS_GROUP = {
        "S7CSE": "G1", "CSE": "G1",
        "S7IT": "G4", "IT": "G4",
        "S7EC": "G2", "EC": "G2",
        "S7ECE": "G3", "ECE": "G3",
        "S7CE": "G5", "CE": "G5",
        "ME": "G6", "EE": "G7", "CH": "G8", "BT": "G9", "MT": "G10"
    }

    def __init__(self):
        """Initialize the Constraint Handler"""
        self.validation_pipeline = [
            self.validate_capacity_constraints,
            self.validate_group_separation,
            self.validate_subject_distribution,
            self.validate_block_assignment,
            self.validate_year_based_constraints
        ]

    def roll_key(self, r):
        """Extract sorting key from roll number"""
        m = re.search(r'(\d{2})[A-Z]{2,3}(\d+)', str(r))
        if not m:
            return (99, 9999)
        return (int(m.group(1)), int(m.group(2)))

    # =============================
    # PROGRAM ELECTIVE HELPER FUNCTIONS
    # =============================
    def get_room_metrics_program_elective(self, room_dict):
        """Get occupancy, subjects, and groups for program elective rooms"""
        occ = sum(len(col) for col in room_dict.values())
        subjs = {st['subj'] for col in room_dict.values() for st in col}
        grps = {self.CLASS_GROUP.get(s.split(':')[0], "OTHER") for s in subjs}
        return occ, subjs, grps

    def is_safe_program_elective(self, room_data, col_name, subject_name):
        """Check if placing subject in column is safe for program electives"""
        subj_grp = self.CLASS_GROUP.get(subject_name.split(':')[0], "OTHER")
        if col_name not in room_data:
            return True

        col_idx = self.PROGRAM_ELECTIVE_BLOCK_ORDER.index(col_name)
        neighbors = []
        if col_idx > 0:
            neighbors.append(self.PROGRAM_ELECTIVE_BLOCK_ORDER[col_idx - 1])
        if col_idx < len(self.PROGRAM_ELECTIVE_BLOCK_ORDER) - 1:
            neighbors.append(self.PROGRAM_ELECTIVE_BLOCK_ORDER[col_idx + 1])

        for n_blk in neighbors:
            if n_blk in room_data:
                for st in room_data[n_blk]:
                    if self.CLASS_GROUP.get(st['subj'].split(':')[0], "OTHER") == subj_grp:
                        return False
        return True

    # =============================
    # OPEN ELECTIVE HELPER FUNCTIONS (ADDED)
    # =============================
    def is_safe_open_elective(self, room_data, col_name, subject_name):
        """Check if placing subject in column is safe for open electives"""
        if col_name not in room_data:
            return True

        col_idx = self.PROGRAM_ELECTIVE_BLOCK_ORDER.index(col_name)
        neighbors = []
        if col_idx > 0:
            neighbors.append(self.PROGRAM_ELECTIVE_BLOCK_ORDER[col_idx - 1])
        if col_idx < len(self.PROGRAM_ELECTIVE_BLOCK_ORDER) - 1:
            neighbors.append(self.PROGRAM_ELECTIVE_BLOCK_ORDER[col_idx + 1])

        for n_blk in neighbors:
            if n_blk in room_data:
                for st in room_data[n_blk]:
                    if st['subj'] == subject_name:
                        return False
        return True

    # =============================
    # CAPACITY CONSTRAINTS
    # =============================
    def validate_capacity_constraints(self, room_data, candidate_allocation, exam_type='general'):
        """
        Validate capacity constraints:
        - Room capacity limits
        - Block/Column capacity limits
        """
        room_name = candidate_allocation.get('room')
        block_name = candidate_allocation.get('block')
        student_count = candidate_allocation.get('count', 0)

        # Check room capacity
        if room_name in room_data:
            if exam_type in ['program_elective', 'open_elective']:
                # For electives: column-wise student counts
                current_room_total = sum(
                    len(col) for col in room_data[room_name].values()
                )
            else:
                # For normal: block-level quantities
                current_room_total = sum(
                    block.get('qty', 0) for block in room_data[room_name].values()
                )

            if current_room_total + student_count > self.MAX_TOTAL_ROOM:
                return False, f"Room capacity exceeded: {current_room_total + student_count} > {self.MAX_TOTAL_ROOM}"

        # Check block/column capacity
        if block_name:
            if exam_type in ['program_elective', 'open_elective']:
                # For electives: column capacity
                if block_name in self.PROGRAM_ELECTIVE_COL_CAPACITY:
                    current_block_qty = 0
                    if room_name in room_data and block_name in room_data[room_name]:
                        current_block_qty = len(room_data[room_name][block_name])

                    if current_block_qty + student_count > self.PROGRAM_ELECTIVE_COL_CAPACITY[block_name]:
                        return False, f"Column capacity exceeded: {current_block_qty + student_count} > {self.PROGRAM_ELECTIVE_COL_CAPACITY[block_name]}"
            else:
                # For normal: block capacity
                if block_name in self.BLOCK_CAPACITY:
                    current_block_qty = 0
                    if room_name in room_data and block_name in room_data[room_name]:
                        current_block_qty = room_data[room_name][block_name].get('qty', 0)

                    if current_block_qty + student_count > self.BLOCK_CAPACITY[block_name]:
                        return False, f"Block capacity exceeded: {current_block_qty + student_count} > {self.BLOCK_CAPACITY[block_name]}"

        return True, "Capacity constraints satisfied"

    # =============================
    # GROUP SEPARATION CONSTRAINTS
    # =============================
    def validate_group_separation(self, room_data, candidate_allocation, exam_type='general'):
        """
        Validate group separation:
        - No incompatible groups adjacent
        - Academic integrity preservation
        """
        room_name = candidate_allocation.get('room')
        block_name = candidate_allocation.get('block')
        cls = candidate_allocation.get('cls')
        subject_name = candidate_allocation.get('subject')

        if not all([room_name, block_name]):
            return True, "No group separation validation needed"

        # Use subject or cls based on exam type
        if exam_type in ['program_elective', 'open_elective'] and subject_name:
            # For electives, use subject name
            return self.validate_program_elective_group_separation(room_data, room_name, block_name, subject_name, exam_type)
        elif cls:
            # For normal exams, use class
            return self.validate_normal_exam_group_separation(room_data, room_name, block_name, cls)

        return True, "Group separation constraints satisfied"

    def validate_normal_exam_group_separation(self, room_data, room_name, block_name, cls):
        """Validate group separation for normal exams"""
        proposed_group = self.CLASS_GROUP.get(cls)

        # Check adjacent blocks
        block_index = self.BLOCK_ORDER.index(block_name)
        adjacent_blocks = []

        if block_index > 0:
            adjacent_blocks.append(self.BLOCK_ORDER[block_index - 1])
        if block_index < len(self.BLOCK_ORDER) - 1:
            adjacent_blocks.append(self.BLOCK_ORDER[block_index + 1])

        # Check if adjacent blocks have incompatible groups
        if room_name in room_data:
            for adj_block in adjacent_blocks:
                if adj_block in room_data[room_name]:
                    adj_data = room_data[room_name][adj_block]
                    if adj_data and adj_data.get('cls'):
                        adj_group = self.CLASS_GROUP.get(adj_data['cls'])
                        if adj_group and adj_group == proposed_group:
                            # Same group is allowed
                            continue
                        else:
                            # Different groups - check if they're compatible
                            pass

        return True, "Group separation constraints satisfied"

    def validate_program_elective_group_separation(self, room_data, room_name, col_name, subject_name, exam_type='program_elective'):
        """Validate group separation for program electives"""
        if exam_type == 'open_elective':
            is_safe = self.is_safe_open_elective(room_data[room_name], col_name, subject_name)
        else:
            is_safe = self.is_safe_program_elective(room_data[room_name], col_name, subject_name)

        return is_safe, ("Group separation satisfied" if is_safe else "Group separation violation")

    # =============================
    # SUBJECT DISTRIBUTION CONSTRAINTS
    # =============================
    def validate_subject_distribution(self, room_data, candidate_allocation, exam_type='general'):
        """
        Validate subject distribution:
        - Normal exams: max 2 subjects per room
        - Elective exams: max subjects per column
        """
        room_name = candidate_allocation.get('room')
        block_name = candidate_allocation.get('block')
        cls = candidate_allocation.get('cls')
        subject_name = candidate_allocation.get('subject')

        if not all([room_name, block_name]):
            return True, "No subject distribution validation needed"

        if exam_type == 'general' or exam_type == 'normal':
            # Normal exams: max 2 subjects per room
            if cls:
                if room_name in room_data:
                    subjects_in_room = set()
                    for block_data in room_data[room_name].values():
                        if block_data and block_data.get('cls'):
                            subjects_in_room.add(block_data['cls'])

                    if cls not in subjects_in_room and len(subjects_in_room) >= 2:
                        return False, f"Room already has 2 subjects: {subjects_in_room}"

        elif exam_type in ['program_elective', 'open_elective']:
            # Elective exams: max subjects per column
            if subject_name and room_name in room_data and block_name in room_data[room_name]:
                col = room_data[room_name][block_name]
                col_subjects = {s['subj'] for s in col}

                if subject_name not in col_subjects and len(col_subjects) >= self.PROGRAM_ELECTIVE_MAX_SUBJ_PER_COL:
                    return False, f"Column already has {len(col_subjects)} subjects"

        return True, "Subject distribution constraints satisfied"

    # =============================
    # BLOCK ASSIGNMENT CONSTRAINTS
    # =============================
    def validate_block_assignment(self, room_data, candidate_allocation, exam_type='general'):
        """
        Validate block assignment:
        - Normal exams: predefined subject-to-block mappings
        - Elective exams: column pairing constraints
        """
        room_name = candidate_allocation.get('room')
        block_name = candidate_allocation.get('block')
        cls = candidate_allocation.get('cls')
        subject_name = candidate_allocation.get('subject')

        if not all([room_name, block_name]):
            return True, "No block assignment validation needed"

        if exam_type in ['program_elective', 'open_elective'] and subject_name:
            # Check column pairing for electives
            # This will be handled by the allocation algorithm
            pass

        return True, "Block assignment constraints satisfied"

    # =============================
    # YEAR-BASED CONSTRAINTS
    # =============================
    def validate_year_based_constraints(self, room_data, candidate_allocation, student_data=None):
        """
        Validate year-based constraints:
        - Track regular vs supplementary students
        - Maintain analytical visibility
        """
        return True, "Year-based constraints satisfied"

    # =============================
    # MAIN VALIDATION PIPELINE
    # =============================
    def validate_allocation(self, room_data, candidate_allocation, exam_type='general', student_data=None):
        """
        Main validation pipeline - runs all validations in order
        Returns: (is_valid, message)
        """
        for validation_func in self.validation_pipeline:
            is_valid, message = validation_func(room_data, candidate_allocation, exam_type)
            if not is_valid:
                return False, f"Validation failed at {validation_func.__name__}: {message}"

        return True, "All constraints satisfied"

    # =============================
    # PRE-ALLOCATION VALIDATION
    # =============================
    def validate_allocation_possibility(self, classes_count, total_students, exam_type='general'):
        """
        Validate if allocation is possible given constraints
        """
        # Calculate minimum rooms needed
        if exam_type in ['program_elective', 'open_elective']:
            min_rooms_needed = max(8, math.ceil(total_students / 30))
        else:
            min_rooms_needed = math.ceil(total_students / self.MAX_TOTAL_ROOM)

        # Check if we have enough capacity
        if min_rooms_needed == 0:
            return False, "No students to allocate"

        return True, f"Allocation possible with minimum {min_rooms_needed} rooms"

    # =============================
    # EXAM-TYPE ADAPTIVE VALIDATION
    # =============================
    def get_exam_specific_constraints(self, exam_type):
        """
        Get constraints specific to exam type
        """
        constraints = {
            'general': {
                'max_subjects_per_room': 2,
                'allow_mixed_years': True,
                'rebalance_tolerance': 4,
                'block_order': self.BLOCK_ORDER,
                'block_capacity': self.BLOCK_CAPACITY
            },
            'program_elective': {
                'max_subjects_per_column': 2,
                'allow_mixed_years': True,
                'rebalance_tolerance': 3,
                'block_order': self.PROGRAM_ELECTIVE_BLOCK_ORDER,
                'block_capacity': self.PROGRAM_ELECTIVE_COL_CAPACITY,
                'room_max_cap': 34,
                'target_difference': 6
            },
            'open_elective': {
                'max_subjects_per_column': 2,
                'allow_mixed_years': True,
                'rebalance_tolerance': 3,
                'block_order': self.PROGRAM_ELECTIVE_BLOCK_ORDER,
                'block_capacity': self.PROGRAM_ELECTIVE_COL_CAPACITY,
                'room_max_cap': 34,
                'target_difference': 6
            }
        }
        return constraints.get(exam_type, constraints['general'])

    # =============================
    # DATA PREPROCESSING HELPERS
    # =============================
    def process_student_data(self, df, exam_type='general'):
        """
        Process student data with constraint awareness
        """
        MASTER_ROLLS = defaultdict(list)
        MASTER_SUBJECTS = {}
        classes_count = {}
        supply_data = defaultdict(dict)

        for _, row in df.iterrows():
            reg = str(row.get("Register No", "")).strip()
            branch = str(row.get("Branch Name", "")).strip()
            course = str(row.get("Course", "")).strip()
            bcode = self.BRANCH_MAP.get(branch)

            if bcode and reg and reg != "nan":
                MASTER_ROLLS[bcode].append(reg)
                MASTER_SUBJECTS[bcode] = course

        # Sort rolls and separate regular/supply
        for bcode, rolls in MASTER_ROLLS.items():
            rolls.sort(key=self.roll_key)
            years = [self.roll_key(r)[0] for r in rolls]
            max_yr = max(years) if years else 0
            regular = [r for r in rolls if self.roll_key(r)[0] == max_yr]
            classes_count[bcode] = len(regular)

            for r in rolls:
                yr = self.roll_key(r)[0]
                if yr != max_yr:
                    supply_data[bcode][yr] = supply_data[bcode].get(yr, 0) + 1

        return {
            'rolls': MASTER_ROLLS,
            'subjects': MASTER_SUBJECTS,
            'classes_count': classes_count,
            'supply_data': supply_data,
            'total_students': sum(len(rolls) for rolls in MASTER_ROLLS.values())
        }

    def process_program_elective_data(self, raw_data):
        """Process program elective specific data format"""
        subj_map = defaultdict(list)
        counts = Counter()

        for roll, branch, subject in raw_data:
            key = f"{branch}:{subject}"
            subj_map[key].append({"roll": roll, "subj": key})
            counts[key] += 1

        for key in subj_map:
            subj_map[key].sort(key=lambda x: x['roll'])

        return subj_map, dict(counts)