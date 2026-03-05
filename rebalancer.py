"""
Rebalancing Module (RBM)
Role: Post-allocation optimization for load balancing across rooms
"""

import math
from collections import defaultdict


class Rebalancer:
    """
    Rebalancing Module - Optimizes student distribution after initial allocation
    """

    def __init__(self, constraint_handler):
        """
        Initialize with reference to constraint handler for validation
        """
        self.chm = constraint_handler
        self.BLOCK_ORDER = constraint_handler.BLOCK_ORDER
        self.BLOCK_CAPACITY = constraint_handler.BLOCK_CAPACITY
        self.PROGRAM_ELECTIVE_BLOCK_ORDER = constraint_handler.PROGRAM_ELECTIVE_BLOCK_ORDER
        self.PROGRAM_ELECTIVE_COL_CAPACITY = constraint_handler.PROGRAM_ELECTIVE_COL_CAPACITY

    # =============================
    # LOAD COMPUTATION
    # =============================
    def compute_room_loads(self, layout, exam_type='general'):
        """
        Compute effective occupancy of each room
        Adapts to exam type: block-level for normal, column-wise for electives
        """
        room_loads = {}

        for room_name, blocks in layout.items():
            if exam_type in ['program_elective', 'open_elective']:
                # For electives: column-wise counts
                total = 0
                for block_name, block_data in blocks.items():
                    if block_data and isinstance(block_data, list):
                        total += len(block_data)
                    elif block_data and 'students' in block_data:
                        total += len(block_data['students'])
                    elif block_data and 'qty' in block_data:
                        total += block_data.get('qty', 0)
            else:
                # For normal: block-level quantities
                total = sum(block.get('qty', 0) for block in blocks.values())

            if total > 0:
                room_loads[room_name] = total

        return room_loads

    def identify_active_rooms(self, room_loads):
        """Identify rooms that are actually in use"""
        return {room: load for room, load in room_loads.items() if load > 0}

    def compute_average_load(self, room_loads):
        """Compute average occupancy across active rooms"""
        active_rooms = self.identify_active_rooms(room_loads)
        if not active_rooms:
            return 0
        return sum(active_rooms.values()) / len(active_rooms)

    # =============================
    # IMBALANCE CLASSIFICATION
    # =============================
    def classify_rooms(self, room_loads, exam_type='general'):
        """
        Classify rooms as overloaded/underloaded based on tolerance
        """
        avg_load = self.compute_average_load(room_loads)

        # Get exam-specific tolerance
        constraints = self.chm.get_exam_specific_constraints(exam_type)
        tolerance = constraints.get('rebalance_tolerance', 4)

        overloaded = {}
        underloaded = {}
        balanced = {}

        for room, load in room_loads.items():
            if load > avg_load + tolerance:
                overloaded[room] = load
            elif load < avg_load - tolerance:
                underloaded[room] = load
            else:
                balanced[room] = load

        return {
            'overloaded': overloaded,
            'underloaded': underloaded,
            'balanced': balanced,
            'average': avg_load,
            'tolerance': tolerance
        }

    # =============================
    # PROGRAM ELECTIVE REBALANCING
    # =============================
    def rebalance_program_elective(self, rooms, exam_type='program_elective'):
        """Specialized rebalancing for program electives"""
        # Get room totals
        room_totals = {}
        for r_name, room_data in rooms.items():
            occ = sum(len(col) for col in room_data.values())
            if occ > 0:
                room_totals[r_name] = occ

        if len(room_totals) < 2:
            return rooms

        avg_occupancy = sum(room_totals.values()) / len(room_totals)

        for _ in range(10):
            overfull = [(r, t) for r, t in room_totals.items() if t > avg_occupancy + 2]
            underfull = [(r, t) for r, t in room_totals.items() if t < avg_occupancy - 2]

            if not overfull or not underfull:
                break

            overfull.sort(key=lambda x: x[1] - avg_occupancy, reverse=True)
            underfull.sort(key=lambda x: avg_occupancy - x[1], reverse=True)

            source_room = overfull[0][0]
            target_room = underfull[0][0]

            moved = False
            for blk in self.PROGRAM_ELECTIVE_BLOCK_ORDER:
                source_col = rooms[source_room][blk]
                if len(source_col) > 2:
                    student_to_move = source_col.pop()

                    for target_blk in self.PROGRAM_ELECTIVE_BLOCK_ORDER:
                        target_col = rooms[target_room][target_blk]
                        if len(target_col) < self.PROGRAM_ELECTIVE_COL_CAPACITY[target_blk]:
                            # Validate using CHM
                            candidate = {
                                'room': target_room,
                                'block': target_blk,
                                'subject': student_to_move['subj'],
                                'count': 1,
                                'cls': student_to_move['subj'].split(':')[0]
                            }

                            is_valid, _ = self.chm.validate_allocation(
                                {target_room: rooms[target_room]}, candidate, exam_type
                            )

                            if is_valid:
                                target_col.append(student_to_move)
                                room_totals[source_room] -= 1
                                room_totals[target_room] += 1
                                moved = True
                                break

                    if moved:
                        break

            if not moved:
                break

        return rooms

    # =============================
    # OPEN ELECTIVE REBALANCING (ADDED)
    # =============================
    def rebalance_open_elective(self, rooms, exam_type='open_elective'):
        """Specialized rebalancing for open electives"""
        # Get room totals
        room_totals = {}
        for r_name, room_data in rooms.items():
            occ = sum(len(col) for col in room_data.values())
            if occ > 0:
                room_totals[r_name] = occ

        if len(room_totals) < 2:
            return rooms

        avg_occupancy = sum(room_totals.values()) / len(room_totals)

        for _ in range(20):
            overfull = [(r, t) for r, t in room_totals.items() if t > avg_occupancy + 2]
            underfull = [(r, t) for r, t in room_totals.items() if t < avg_occupancy - 2]

            if not overfull or not underfull:
                break

            overfull.sort(key=lambda x: x[1] - avg_occupancy, reverse=True)
            underfull.sort(key=lambda x: avg_occupancy - x[1], reverse=True)

            source_room = overfull[0][0]
            target_room = underfull[0][0]

            moved = False
            for blk in self.PROGRAM_ELECTIVE_BLOCK_ORDER:
                source_col = rooms[source_room][blk]
                if len(source_col) > 2:
                    for student_idx in range(len(source_col)):
                        student_to_move = source_col[student_idx]
                        
                        for target_blk in self.PROGRAM_ELECTIVE_BLOCK_ORDER:
                            target_col = rooms[target_room][target_blk]
                            if len(target_col) < self.PROGRAM_ELECTIVE_COL_CAPACITY[target_blk]:
                                # Validate using CHM
                                candidate = {
                                    'room': target_room,
                                    'block': target_blk,
                                    'subject': student_to_move['subj'],
                                    'count': 1
                                }

                                is_valid, _ = self.chm.validate_allocation(
                                    {target_room: rooms[target_room]}, candidate, exam_type
                                )

                                if is_valid:
                                    source_col.pop(student_idx)
                                    target_col.append(student_to_move)
                                    room_totals[source_room] -= 1
                                    room_totals[target_room] += 1
                                    moved = True
                                    break
                        
                        if moved:
                            break

                if moved:
                    break

            if not moved:
                break

        return rooms

    # =============================
    # TRANSFER MECHANISMS
    # =============================
    def find_transfer_candidates(self, layout, classification, exam_type='general'):
        """
        Find possible transfers from overloaded to underloaded rooms
        """
        transfers = []

        overloaded = classification['overloaded']
        underloaded = classification['underloaded']

        if not overloaded or not underloaded:
            return transfers

        # Sort rooms by degree of imbalance
        sorted_overloaded = sorted(overloaded.items(), key=lambda x: x[1], reverse=True)
        sorted_underloaded = sorted(underloaded.items(), key=lambda x: x[1])

        for src_room, src_load in sorted_overloaded:
            for tgt_room, tgt_load in sorted_underloaded:
                if src_load <= tgt_load:
                    continue

                # Find transferable units
                transfer_units = self.find_transferable_units(
                    layout, src_room, tgt_room, exam_type
                )

                if transfer_units:
                    transfers.append({
                        'source': src_room,
                        'target': tgt_room,
                        'units': transfer_units,
                        'source_load': src_load,
                        'target_load': tgt_load
                    })
                    break  # One transfer per source room per iteration

            if transfers:
                break  # One transfer per iteration

        return transfers

    def find_transferable_units(self, layout, src_room, tgt_room, exam_type='general'):
        """
        Find minimal transferable units based on exam type
        """
        transfer_units = []

        if exam_type in ['program_elective', 'open_elective']:
            # Student-level transfers for electives
            block_order = self.PROGRAM_ELECTIVE_BLOCK_ORDER
            for block_name in block_order:
                src_block = layout[src_room].get(block_name, [])

                if len(src_block) > 2:
                    # Can transfer individual students
                    student = src_block[-1]
                    transfer_units.append({
                        'type': 'student',
                        'block': block_name,
                        'cls': student['subj'].split(':')[0],
                        'subject': student['subj'],
                        'count': 1,
                        'student': student
                    })
        else:
            # Quantity-based block transfers for normal exams
            for block_name in self.BLOCK_ORDER:
                src_block = layout[src_room].get(block_name, {})

                if src_block and src_block.get('qty', 0) > 3:
                    transfer_units.append({
                        'type': 'quantity',
                        'block': block_name,
                        'cls': src_block.get('cls'),
                        'count': min(2, src_block['qty'] - 3),
                        'subject': src_block.get('subject')
                    })

        return transfer_units

    # =============================
    # TRANSFER VALIDATION
    # =============================
    def validate_transfer(self, layout, transfer, exam_type='general'):
        """
        Validate transfer against constraints using CHM
        """
        source_room = transfer['source']
        target_room = transfer['target']

        for unit in transfer['units']:
            # Create candidate allocation for validation
            candidate = {
                'room': target_room,
                'block': unit['block'],
                'cls': unit.get('cls'),
                'subject': unit.get('subject'),
                'count': unit['count']
            }

            # Validate using CHM
            is_valid, message = self.chm.validate_allocation(
                layout, candidate, exam_type
            )

            if not is_valid:
                return False, f"Transfer invalid: {message}"

        return True, "Transfer valid"

    # =============================
    # TRANSFER EXECUTION
    # =============================
    def execute_transfer(self, layout, transfer):
        """
        Execute validated transfer
        """
        source_room = transfer['source']
        target_room = transfer['target']

        for unit in transfer['units']:
            block_name = unit['block']
            count = unit['count']

            if unit['type'] == 'student':
                # Move individual student
                student = unit['student']

                # Remove from source
                src_block = layout[source_room][block_name]
                src_block.remove(student)

                # Add to target
                if block_name not in layout[target_room]:
                    layout[target_room][block_name] = []

                layout[target_room][block_name].append(student)

            elif unit['type'] == 'quantity':
                # Move quantities
                src_block = layout[source_room][block_name]
                src_block['qty'] -= count

                # Add to target
                if block_name not in layout[target_room] or not layout[target_room][block_name]:
                    layout[target_room][block_name] = {
                        'cls': unit['cls'],
                        'qty': count,
                        'subject': unit.get('subject')
                    }
                else:
                    layout[target_room][block_name]['qty'] += count

        return layout

    # =============================
    # MAIN REBALANCING LOOP
    # =============================
    def rebalance(self, layout, exam_type='general', max_iterations=50):
        """
        Main rebalancing loop
        Returns: (rebalanced_layout, stats)
        """
        if not layout:
            return layout, {'success': False, 'reason': 'Empty layout'}

        # Use specialized rebalancing for program electives
        if exam_type == 'program_elective':
            rebalanced_layout = self.rebalance_program_elective(layout, exam_type)

            # Compute final stats
            final_loads = self.compute_room_loads(rebalanced_layout, exam_type)
            loads_list = list(final_loads.values())

            stats = {
                'success': True,
                'iterations': 1,
                'transfers_performed': [],
                'initial_imbalance': 0,
                'final_imbalance': max(loads_list) - min(loads_list) if loads_list else 0,
                'average_load': self.compute_average_load(final_loads),
                'overloaded_rooms': 0,
                'underloaded_rooms': 0
            }

            return rebalanced_layout, stats
            
        # Use specialized rebalancing for open electives
        elif exam_type == 'open_elective':
            rebalanced_layout = self.rebalance_open_elective(layout, exam_type)

            # Compute final stats
            final_loads = self.compute_room_loads(rebalanced_layout, exam_type)
            loads_list = list(final_loads.values())

            stats = {
                'success': True,
                'iterations': 1,
                'transfers_performed': [],
                'initial_imbalance': 0,
                'final_imbalance': max(loads_list) - min(loads_list) if loads_list else 0,
                'average_load': self.compute_average_load(final_loads),
                'overloaded_rooms': 0,
                'underloaded_rooms': 0
            }

            return rebalanced_layout, stats

        # Original rebalancing for normal exams
        current_layout = layout.copy()
        iteration = 0
        transfers_performed = []

        while iteration < max_iterations:
            # Compute current loads
            room_loads = self.compute_room_loads(current_layout, exam_type)

            # Classify rooms
            classification = self.classify_rooms(room_loads, exam_type)

            # Check if balanced
            if not classification['overloaded'] and not classification['underloaded']:
                break

            # Find transfer candidates
            transfers = self.find_transfer_candidates(
                current_layout, classification, exam_type
            )

            if not transfers:
                break  # No possible transfers

            # Validate and execute best transfer
            for transfer in transfers:
                is_valid, message = self.validate_transfer(
                    current_layout, transfer, exam_type
                )

                if is_valid:
                    # Execute transfer
                    current_layout = self.execute_transfer(current_layout, transfer)
                    transfers_performed.append({
                        'iteration': iteration,
                        'source': transfer['source'],
                        'target': transfer['target'],
                        'units': len(transfer['units'])
                    })
                    break  # Execute one transfer per iteration
            else:
                # No valid transfers found
                break

            iteration += 1

        # Compute final stats
        final_loads = self.compute_room_loads(current_layout, exam_type)
        final_classification = self.classify_rooms(final_loads, exam_type)

        stats = {
            'success': True,
            'iterations': iteration,
            'transfers_performed': transfers_performed,
            'initial_imbalance': self.calculate_imbalance_metric(room_loads),
            'final_imbalance': self.calculate_imbalance_metric(final_loads),
            'average_load': final_classification['average'],
            'overloaded_rooms': len(final_classification['overloaded']),
            'underloaded_rooms': len(final_classification['underloaded'])
        }

        return current_layout, stats

    def calculate_imbalance_metric(self, room_loads):
        """Calculate a metric for room imbalance"""
        if not room_loads:
            return 0

        loads = list(room_loads.values())
        return max(loads) - min(loads)

    # =============================
    # POST-REBALANCING CLEANUP
    # =============================
    def cleanup_empty_blocks(self, layout, exam_type='general'):
        """
        Remove empty blocks from layout
        """
        for room_name, blocks in layout.items():
            if exam_type in ['program_elective', 'open_elective']:
                # For electives, just ensure lists exist
                for block_name in self.PROGRAM_ELECTIVE_BLOCK_ORDER:
                    if block_name not in blocks:
                        blocks[block_name] = []
            else:
                # For normal, handle dict blocks
                for block_name in list(blocks.keys()):
                    block_data = blocks[block_name]
                    if not block_data:
                        blocks[block_name] = {}
                    elif 'students' in block_data and not block_data['students']:
                        blocks[block_name] = {}
                    elif 'qty' in block_data and block_data['qty'] == 0:
                        blocks[block_name] = {}

        return layout