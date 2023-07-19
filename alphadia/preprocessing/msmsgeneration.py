"""Create MSMS spectra."""

import logging

import numpy as np
import pandas as pd

import alphatims.utils
import alphatims.tempmmap as tm
import alphabase.io.hdf
import alphadia.preprocessing.peakstats


class MSMSGenerator:

    def __init__(
        self,
        scan_tolerance=6,
        subcycle_tolerance=3,
        cycle_sigma=3,
        scan_sigma=6,
    ):
        self.scan_tolerance = scan_tolerance
        self.subcycle_tolerance = subcycle_tolerance
        self.cycle_sigma = cycle_sigma
        self.scan_sigma = scan_sigma

    def set_dia_data(self, dia_data):
        self.dia_data = dia_data

    def set_peak_collection(self, peak_collection):
        self.peak_collection = peak_collection

    def set_connector(self, connector):
        self.connector = connector

    def set_deisotoper(self, deisotoper):
        self.deisotoper = deisotoper

    def set_peak_stats_calculator(self, peak_stats_calculator):
        self.peak_stats_calculator = peak_stats_calculator

    def create_msms_spectra(self):
        import multiprocessing
        logging.info("Creating MSMS spectra")

        def starfunc(cycle_index):
            return create_precursor_centric_ion_network(
                cycle_index,
                self.peak_collection.indices,
                self.peak_collection.indptr,
                self.dia_data.zeroth_frame,
                self.dia_data.scan_max_index,
                self.scan_tolerance,
                self.subcycle_tolerance,
                self.connector.cycle,
                self.dia_data.mz_values,
                self.dia_data.tof_indices,
                np.isin(self.peak_collection.indices, self.deisotoper.mono_isotopes)
            )

        precursor_indices = []
        precursor_counts = [[0]]
        fragment_indices = []

        iterable = range(
            len(self.dia_data.push_indptr) // np.prod(self.dia_data.cycle.shape[:-1]) + 1
        )

        with multiprocessing.pool.ThreadPool(alphatims.utils.MAX_THREADS) as pool:
            for (
                precursor_indices_,
                precursor_counts_,
                fragment_indices_,
            ) in alphatims.utils.progress_callback(
                pool.imap(starfunc, iterable),
                total=len(iterable),
                include_progress_callback=True
            ):
                precursor_indices.append(precursor_indices_)
                precursor_counts.append(precursor_counts_)
                fragment_indices.append(fragment_indices_)

        precursor_indices = np.concatenate(precursor_indices)
        precursor_counts = np.cumsum(np.concatenate(precursor_counts))
        fragment_indices = np.concatenate(fragment_indices)
        self.precursor_indices = tm.clone(precursor_indices)
        self.precursor_indptr = tm.clone(precursor_counts)
        self.fragment_indices = tm.clone(fragment_indices)
        self.set_fragment_apex_distances()
        self.set_fragment_profile_distances()
        self.set_fragment_quad_overlaps()
        self.fragment_frequencies = self.mobilogram_correlations * self.xic_correlations * self.quad_overlaps

    def set_fragment_apex_distances(self):
        logging.info("Setting fragment-precursor apex distances")
        dia_data = self.dia_data
        fdf = pd.DataFrame(
            dia_data.convert_from_indices(
                self.fragment_indices,
                return_scan_indices=True,
                return_push_indices=True,
            )
        )
        pdf = pd.DataFrame(
            dia_data.convert_from_indices(
                np.repeat(
                    self.precursor_indices,
                    np.diff(self.precursor_indptr)
                ),
                return_scan_indices=True,
                return_push_indices=True,
            )
        )
        pdf["cycle"] = (pdf.push_indices - dia_data.zeroth_frame * dia_data.scan_max_index) // np.prod(dia_data.cycle.shape[:-1])
        fdf["cycle"] = (fdf.push_indices - dia_data.zeroth_frame * dia_data.scan_max_index) // np.prod(dia_data.cycle.shape[:-1])
        self.apex_distances = (
            np.exp(
                -((pdf.scan_indices - fdf.scan_indices) / self.scan_sigma)**2 / 2
            ) * np.exp(
                -((pdf.cycle - fdf.cycle) / self.cycle_sigma)**2 / 2
            )
        ).values
        self.fragment_frequencies = self.apex_distances

    def set_fragment_profile_distances(self):
        logging.info("Setting fragment-precursor correlations")
        self.xic_correlations = np.empty(len(self.fragment_indices))
        self.mobilogram_correlations = np.empty(len(self.fragment_indices))
        fragment_peak_indices = np.searchsorted(
            self.peak_collection.indices,
            self.fragment_indices,
        )
        precursor_peak_indices = np.searchsorted(
            self.peak_collection.indices,
            self.precursor_indices,
        )
        alphadia.preprocessing.peakstats.set_profile_correlations(
            range(len(precursor_peak_indices)),
            # range(10),
            # 0,
            fragment_peak_indices,
            precursor_peak_indices,
            self.precursor_indptr,
            self.xic_correlations,
            self.peak_stats_calculator.xic_offset,
            self.peak_stats_calculator.xics,
            self.peak_stats_calculator.xic_indptr,
        )
        alphadia.preprocessing.peakstats.set_profile_correlations(
            range(len(precursor_peak_indices)),
            # range(10),
            # 0,
            fragment_peak_indices,
            precursor_peak_indices,
            self.precursor_indptr,
            self.mobilogram_correlations,
            self.peak_stats_calculator.mobilogram_offset,
            self.peak_stats_calculator.mobilograms,
            self.peak_stats_calculator.mobilogram_indptr,
        )

    def set_fragment_quad_overlaps(self):
        logging.info("Setting fragment-precursor quad overlaps")
        self.quad_overlaps = np.empty_like(
            self.apex_distances
        )
        calculate_quad_overlap(
            range(len(self.precursor_indices)),
            self.precursor_indices,
            self.precursor_indptr,
            self.fragment_indices,
            self.dia_data.mz_values,
            self.dia_data.tof_indices,
            self.dia_data.push_indptr,
            self.dia_data.zeroth_frame,
            self.dia_data.cycle,
            self.peak_stats_calculator.peakfinder.cluster_assemblies,
            self.dia_data.intensity_values.astype(np.float32),
            self.quad_overlaps,
        )

    def get_ms1_df(self):
        logging.info("Creating MS1 dataframe")
        precursor_index_mask = np.isin(
            self.peak_collection.indices,
            self.precursor_indices
        )
        ms1_df = self.peak_stats_calculator.as_dataframe(
            precursor_index_mask,
            append_apices=True
        )
        ms1_df['charge'] = np.array([2, 3])[
            np.isin(
                self.precursor_indices,
                self.deisotoper.mono_isotopes_charge3
            ).astype(np.int)
        ]
        ms1_df['fragment_start'] = self.precursor_indptr[:-1]
        ms1_df['fragment_end'] = self.precursor_indptr[1:]
        return ms1_df

    def get_ms2_df(self):
        logging.info("Creating MS2 dataframe")
        fragment_order = np.arange(len(self.fragment_indices))
        sort_ms2_fragments_by_tof_indices(
            range(len(self.precursor_indices)),
            self.fragment_indices,
            self.precursor_indptr,
            self.dia_data.tof_indices,
            fragment_order,
        )
        fragment_indices = np.searchsorted(
            self.peak_collection.indices,
            self.fragment_indices[fragment_order],
        )
        ms2_df = self.peak_stats_calculator.as_dataframe(
            fragment_indices,
            append_apices=True
        )
        # temp = np.empty(len(ms2_df))
        ms2_df["apex_correlation"] = self.apex_distances[
            fragment_order
        ]
        ms2_df["xic_correlations"] = self.xic_correlations[
            fragment_order
        ]
        ms2_df["mobilogram_correlations"] = self.mobilogram_correlations[
            fragment_order
        ]
        ms2_df["quad_correlation"] = self.quad_overlaps[
            fragment_order
        ]
        return ms2_df

    def write_to_hdf_file(self, file_name=None):
        if file_name is None:
            import os
            file_name = os.path.join(
                self.dia_data.bruker_d_folder_name,
                "pseudo_spectra.hdf"
            )
        ms1_df = self.get_ms1_df()
        ms2_df = self.get_ms2_df()
        hdf = alphabase.io.hdf.HDF_File(
            file_name,
            read_only=False,
            truncate=True,
        )
        hdf.precursors = ms1_df
        hdf.fragments = ms2_df
        return hdf


@alphatims.utils.njit(nogil=True)
def create_precursor_centric_ion_network(
    cycle_index,
    indices,
    indptr,
    zeroth_frame,
    scan_max_index,
    scan_tolerance,
    subcycle_tolerance,
    mz_windows,
    mz_values,
    tof_indices,
    is_mono,
):
    subcycle_count = mz_windows.shape[0]
    frame_count = mz_windows.shape[1]
    cycle_length = subcycle_count * frame_count * scan_max_index
    push_offset = cycle_length * cycle_index + zeroth_frame * scan_max_index
    precursor_indices = []
    precursor_count = []
    fragment_indices = []
    for self_push_offset in np.flatnonzero(mz_windows[..., 0] == -1):
        self_push_index = push_offset + self_push_offset
        if self_push_index > len(indptr):
            break
        self_start = indptr[self_push_index]
        self_end = indptr[self_push_index + 1]
        self_scan = self_push_offset % scan_max_index
        self_frame = (self_push_offset // scan_max_index) % subcycle_count
        for precursor_index_ in range(self_start, self_end):
            if not is_mono[precursor_index_]:
                continue
            precursor_index = indices[precursor_index_]
            precursor_mz = mz_values[tof_indices[precursor_index]]
            hits = 0
            for sub_cycle_offset in range(-subcycle_tolerance, subcycle_tolerance + 1):
                for frame_offset in range(-self_frame, frame_count - self_frame):
                    for scan_offset in range(-scan_tolerance, scan_tolerance + 1):
                        other_scan = self_scan + scan_offset
                        if not (0 <= other_scan < scan_max_index):
                            continue

                        other_push_index = self_push_index
                        other_push_index += scan_offset
                        other_push_index += frame_offset * scan_max_index
                        other_push_index += sub_cycle_offset * frame_count * scan_max_index

                        if not (0 <= other_push_index < len(indptr)):
                            continue
                        low_mz, high_mz = mz_windows.reshape((-1, 2))[
                            (other_push_index - zeroth_frame * scan_max_index) % cycle_length
                        ]
                        if not (low_mz <= precursor_mz < high_mz):
                            continue
                        other_start = indptr[other_push_index]
                        other_end = indptr[other_push_index + 1]
                        for fragment_index_ in range(other_start, other_end):
                            fragment_index = indices[fragment_index_]
                            fragment_indices.append(fragment_index)
                            hits += 1
            if hits > 0:
                precursor_indices.append(precursor_index)
                precursor_count.append(hits)
    return (
        np.array(precursor_indices),
        np.array(precursor_count),
        np.array(fragment_indices),
    )


@alphatims.utils.pjit
def sort_ms2_fragments_by_tof_indices(
    index,
    fragment_indices,
    indptr,
    tof_indices,
    fragment_order
):
    start = indptr[index]
    end = indptr[index + 1]
    selected_fragment_indices = fragment_indices[start:end]
    selected_tof_indices = tof_indices[selected_fragment_indices]
    order = np.argsort(selected_tof_indices)
    fragment_order[start:end] = fragment_order[start:end][order]


@alphatims.utils.pjit
def calculate_quad_overlap(
    index,
    precursor_indices,
    precursor_indptr,
    fragment_indices,
    mz_values,
    tof_indices,
    push_indptr,
    zeroth_frame,
    cycle,
    cluster_assemblies,
    intensity_values,
    quad_overlaps,
):
    precursor_index = precursor_indices[index]
    precursor_mz = mz_values[tof_indices[precursor_index]]
    precursor_start = precursor_indptr[index]
    precursor_end = precursor_indptr[index + 1]
    for f_index in range(precursor_start, precursor_end):
        fragment_index = fragment_indices[f_index]
        raw_indices = alphadia.preprocessing.peakstats.get_ions(
            fragment_index,
            cluster_assemblies,
        )
        push_indices = np.searchsorted(
            push_indptr,
            raw_indices,
            "right"
        ) - 1
        cycle_offsets = (push_indices - zeroth_frame * cycle.shape[-2]) % (cycle.size // 2)
        quads = cycle.reshape(-1,2)[cycle_offsets]
        # selected = quads[:,0] <= precursor_mz
        # selected &= precursor_mz <= quads[:,1]
        # result = np.sum(selected) / len(selected)
        # quad_overlaps[f_index] = result
        mzs = np.empty(len(quads) * 2)
        mzs[:len(quads)] = quads[:,0]
        mzs[len(quads):] = quads[:,1]
        ints = np.empty(len(quads) * 2)
        ints[:len(quads)] = intensity_values[raw_indices]
        ints[len(quads):] = -intensity_values[raw_indices]
        order = np.argsort(mzs)
        selected = np.zeros(len(mzs), dtype=np.bool_)
        mzs = mzs[order]
        if len(selected) > 0:
            selected[0] = True
            selected[np.flatnonzero(mzs[:-1] != mzs[1:]) + 1] = True
        mzs = mzs[selected]
        ints = np.cumsum(ints[order])[selected]
        ints /= np.max(ints)
        location = np.searchsorted(mzs, precursor_mz)
        probability = ints[location]
        quad_overlaps[f_index] = probability