"""Smooth dia data intensity values."""

import logging

import numpy as np

import alphatims.utils
import alphatims.tempmmap as tm


class Smoother:

    def __init__(
        self,
        tof_tolerance=3,
        cycle_tolerance=3,
        scan_tolerance=45,
        tof_sigma=0,
        cycle_sigma=3,
        scan_sigma=15,
    ):
        self.tof_tolerance = tof_tolerance
        self.cycle_tolerance = cycle_tolerance
        self.scan_tolerance = scan_tolerance
        self.cycle_sigma = cycle_sigma
        self.scan_sigma = scan_sigma
        self.tof_sigma = tof_sigma

    def set_dia_data(self, dia_data):
        self.dia_data = dia_data

    def set_connector(self, connector):
        self.connector = connector

    def smooth(self):
        logging.info("Smoothing peaks")
        self.smooth_intensity_values = tm.zeros(
            self.dia_data.intensity_values.shape,
            dtype=np.float32
        )
        self.density_values = tm.zeros(
            self.dia_data.intensity_values.shape,
            dtype=np.float32
        )
        self.neighbor_types = tm.zeros(
            self.dia_data.intensity_values.shape,
            dtype=np.uint8
        )
        smooth(
            # 0,
            range(
                len(self.dia_data.push_indptr) // np.prod(
                    self.connector.cycle.shape[:-1]
                ) + 1
            ),
            self.dia_data.push_indptr,
            self.dia_data.tof_indices,
            self.dia_data.intensity_values,
            self.tof_tolerance,
            self.dia_data.scan_max_index,
            self.dia_data.zeroth_frame,
            self.connector.connection_counts,
            self.connector.connections,
            self.cycle_tolerance,
            self.smooth_intensity_values,
            self.neighbor_types,
            self.density_values,
            self.cycle_sigma,
            self.scan_sigma,
            self.tof_sigma,
            np.prod(
                self.connector.cycle.shape[1:-1]
            ),
        )
        self.smooth_intensity_values += self.dia_data.intensity_values


@alphatims.utils.pjit
def smooth(
    cycle_index,
    indptr,
    tof_indices,
    intensity_values,
    tof_tolerance,
    scan_max_index,
    zeroth_frame,
    connection_counts,
    connections,
    cycle_tolerance,
    smooth_intensity_values,
    neighbor_types,
    density_values,
    cycle_sigma,
    scan_sigma,
    tof_sigma,
    subcycle_len,
):
    len_cycle = len(connection_counts) - 1
    push_offset = len_cycle * cycle_index + zeroth_frame * scan_max_index
    for self_connection_index, connection_start in enumerate(
        connection_counts[:-1]
    ):
        connection_end = connection_counts[self_connection_index + 1]
        if connection_end == connection_start:
            continue
        self_push_index = push_offset + self_connection_index
        if self_push_index > len(indptr):
            break
        self_start = indptr[self_push_index]
        self_end = indptr[self_push_index + 1]
        if self_start == self_end:
            continue
        self_scan = self_connection_index % scan_max_index
        max_neighbor_count = 0
        if True:
            self_cycle = (self_push_index - zeroth_frame * scan_max_index) // subcycle_len
            for other_connection_offset in connections[connection_start: connection_end]:
                other_push_index = self_push_index + other_connection_offset
                if other_push_index == self_push_index:
                    continue
                if not (0 <= other_push_index < len(indptr)):
                    continue
                other_scan = other_push_index % scan_max_index
                connection_blur = gauss_correction(
                    self_scan - other_scan,
                    scan_sigma,
                )
                other_cycle = (other_push_index - zeroth_frame * scan_max_index) // subcycle_len
                cycle_offset = self_cycle - other_cycle
                cycle_blur = gauss_correction(cycle_offset, cycle_sigma)
        # for cycle_offset in range(-cycle_tolerance, cycle_tolerance + 1):
        #     cycle_blur = gauss_correction(cycle_offset, cycle_sigma)
        #     for other_connection_index in connections[connection_start: connection_end]:
        #         other_scan = other_connection_index % scan_max_index
        #         connection_blur = gauss_correction(
        #             self_connection_index % scan_max_index - other_connection_index % scan_max_index,
        #             scan_sigma,
        #         )
        #         other_push_index = push_offset + other_connection_index + len_cycle * cycle_offset
        #         if other_push_index == self_push_index:
        #             continue
        #         if other_push_index >= len(indptr):
        #             continue
                max_neighbor_count += 1
                other_start = indptr[other_push_index]
                other_end = indptr[other_push_index + 1]
                if other_start == other_end:
                    continue
                self_index = self_start
                other_index = other_start
                neighbor_type = determine_neighbor_type(
                    cycle_offset,
                    self_scan,
                    other_scan,
                )
                while (self_index < self_end) and (other_index < other_end):
                    self_tof = tof_indices[self_index]
                    other_tof = tof_indices[other_index]
                    if (self_tof - tof_tolerance) <= other_tof <= (self_tof + tof_tolerance):
                        other_intensity = intensity_values[other_index]
                        tof_blur = gauss_correction(
                            int(self_tof) - int(other_tof),
                            tof_sigma,
                        )
                        smooth_intensity_values[self_index] += other_intensity * cycle_blur * connection_blur * tof_blur
                        neighbor_types[self_index] |= neighbor_type
                        density_values[self_index] += 1
                    if self_tof < other_tof:
                        self_index += 1
                    else:
                        other_index += 1
        for self_index in range(self_start, self_end):
            density_values[self_index] /= max_neighbor_count


@alphatims.utils.njit(nogil=True)
def gauss_correction(x=0, sigma=1):
    if sigma == 0:
        return 1
    else:
        return np.exp(-(x / sigma)**2 / 2)


@alphatims.utils.njit(nogil=True)
def determine_neighbor_type(
    cycle_offset,
    self_scan,
    other_scan,
):
    if cycle_offset < 0:
        if self_scan < other_scan:
            return 2**0
        elif self_scan == other_scan:
            return 2**1
        else:
            return 2**2
    elif cycle_offset == 0:
        if self_scan < other_scan:
            return 2**7
        elif self_scan == other_scan:
            return 0
        else:
            return 2**3
    else:
        if self_scan < other_scan:
            return 2**6
        elif self_scan == other_scan:
            return 2**5
        else:
            return 2**4