# native imports
from typing_extensions import Self
import logging
import socket
from pathlib import Path
import yaml
import os
import hashlib
from typing import Union, List, Dict, Tuple, Optional

# alphadia imports
from alphadia.extraction.data import TimsTOFDIA_
from alphadia.extraction.calibration import RunCalibration
from alphadia.extraction.candidateselection import MS1CentricCandidateSelection
from alphadia.extraction.scoring import fdr_correction, unpack_fragment_info, MS2ExtractionWorkflow
from alphadia.extraction import utils

# alpha family imports
import alphatims

import alphabase.psm_reader
import alphabase.peptide.precursor
import alphabase.peptide.fragment
from alphabase.spectral_library.flat import SpecLibFlat
from alphabase.spectral_library.base import SpecLibBase
from alphabase.spectral_library.reader import SWATHLibraryReader

# third party imports
import numpy as np
import pandas as pd 
from matplotlib.style import library
import neptune.new as neptune
from neptune.new.types import File

class Plan:

    def __init__(self, 
            raw_file_list: List,
            config_path : Union[str, None] = None,
            config_update_path : Union[str, None] = None,
            config_update : Union[Dict, None] = None
        ) -> None:
        """Highest level class to plan a DIA Search. 
        Owns the input file list, speclib and the config.
        Performs required manipulation of the spectral library like transforming RT scales and adding columns.

        Parameters
        ----------
        raw_data : list
            list of input file locations

        config_path : str, optional
            yaml file containing the default config.

        config_update_path : str, optional
           yaml file to update the default config.

        config_update : dict, optional
            dict to update the default config. Can be used for debugging purposes etc.

        """

        self.raw_file_list = raw_file_list

        # default config path is not defined in the function definition to account for for different path separators on different OS
        if config_path is None:
            # default yaml config location under /misc/config/config.yaml
            config_path = os.path.join(os.path.dirname(__file__), '..','..','misc','config','default.yaml')

        # 1. load default config
        with open(config_path, 'r') as f:
            logging.info(f'loading default config from {config_path}')
            self.config = yaml.safe_load(f)

        # 2. load update config from yaml file
        if config_update_path is not None:
            logging.info(f'loading config update from {config_update_path}')
            with open(config_update_path, 'r') as f:
                config_update_fromyaml = yaml.safe_load(f)
            utils.recursive_update(self.config, config_update_fromyaml)

        # 3. load update config from dict
        if config_update is not None:
            logging.info(f'Applying config update from dict')
            utils.recursive_update(self.config, config_update)

        

    @property
    def raw_file_list(
            self
        ) -> List[str]:
        """List of input files locations.
        """
        return self._raw_file_list
    
    @raw_file_list.setter
    def raw_file_list(
            self, 
            raw_file_list : List[str]
        ):
        self._raw_file_list = raw_file_list

    @property
    def config(
            self
        ) -> dict:
        """Dict with all configuration parameters for the extraction.
        """
        return self._config
    
    @config.setter
    def config(
            self, 
            config : dict
        ) -> None:
        self._config = config

    @property
    def speclib(
            self
        ) -> SpecLibFlat:
        """Flattened Spectral Library."""
        return self._speclib
    
    @speclib.setter
    def speclib(
            self,
            speclib : SpecLibFlat
        ) -> None:
        self._speclib = speclib

    
    def norm_to_rt(
            self,
            dia_data : alphatims.bruker.TimsTOF, 
            norm_values : np.ndarray, 
            active_gradient_start : Union[float,None] = None, 
            active_gradient_stop : Union[float,None] = None,
            mode = None
        ):
        """Convert normalized retention time values to absolute retention time values.

        Parameters
        ----------
        dia_data : alphatims.bruker.TimsTOF
            TimsTOF object containing the DIA data.

        norm_values : np.ndarray
            Array of normalized retention time values.

        active_gradient_start : float, optional
            Start of the active gradient in seconds, by default None. 
            If None, the value from the config is used. 
            If not defined in the config, it is set to zero.

        active_gradient_stop : float, optional
            End of the active gradient in seconds, by default None.
            If None, the value from the config is used.
            If not defined in the config, it is set to the last retention time value.

        mode : str, optional
            Mode of the gradient, by default None.
            If None, the value from the config is used which should be 'tic' by default

        """

        # retrive the converted absolute intensities
        data = dia_data.frames.query('MsMsType == 0')[[
            'Time', 'SummedIntensities']
        ]
        time = data['Time'].values
        intensity = data['SummedIntensities'].values

        # determine if the gradient start and stop are defined in the config
        if active_gradient_start is None:
            if 'active_gradient_start' in self.config['extraction']:
                lower_rt = self.config['extraction']['active_gradient_start']
            else:
                lower_rt = time[0] + self.config['extraction']['initial_rt_tolerance']/2
        else:
            lower_rt = active_gradient_start

        if active_gradient_stop is None:
            if 'active_gradient_stop' in self.config['extraction']:
                upper_rt = self.config['extraction']['active_gradient_stop']
            else:
                upper_rt = time[-1] - (self.config['extraction']['initial_rt_tolerance']/2)
        else:
            upper_rt = active_gradient_stop

        # determine the mode based on the config or the function parameter
        if mode is None:
            mode = self.config['extraction']['norm_rt_mode'] if 'norm_rt_mode' in self.config['extraction'] else 'tic'
        else:
            mode = mode.lower()

        if mode == 'linear':
            return np.interp(norm_values, [0,1], [lower_rt,upper_rt])
            
        elif mode == 'tic':
            # get lower and upper rt slice
            lower_idx = np.searchsorted(time, lower_rt)
            upper_idx = np.searchsorted(time, upper_rt, side='right')
            time = time[lower_idx:upper_idx]
            intensity = intensity[lower_idx:upper_idx]
            cum_intensity = np.cumsum(intensity)/np.sum(intensity)
            return np.interp(norm_values, cum_intensity, time)

        else:
            raise ValueError(f'Unknown norm_rt_mode {mode}')

    def from_spec_lib_base(self, speclib_base):

        speclib = SpecLibFlat()
        speclib.parse_base_library(speclib_base)

        self.from_spec_lib_flat(speclib)

    def from_spec_lib_flat(self, speclib_flat):

        self.speclib = speclib_flat

        self.rename_columns(self.speclib._precursor_df, 'precursor_columns')
        self.rename_columns(self.speclib._fragment_df, 'fragment_columns')

        self.log_library_stats()

        self.add_precursor_columns(self.speclib.precursor_df)

        output_columns = self.config['extraction']['output_columns']
        existing_columns = self.speclib.precursor_df.columns
        existing_output_columns = [c for c in output_columns if c in existing_columns]

        self.speclib.precursor_df = self.speclib.precursor_df[existing_output_columns]
        self.speclib.precursor_df = self.speclib.precursor_df.sort_values('elution_group_idx')
        self.speclib.precursor_df = self.speclib.precursor_df.reset_index(drop=True)

    def log_library_stats(self):

        logging.info(f'========= Library Stats =========')
        logging.info(f'Number of precursors: {len(self.speclib.precursor_df):,}')

        if 'decoy' in self.speclib.precursor_df.columns:
            n_targets = len(self.speclib.precursor_df.query('decoy == False'))
            n_decoys = len(self.speclib.precursor_df.query('decoy == True'))
            logging.info(f'\tthereof targets:{n_targets:,}')
            logging.info(f'\tthereof decoys: {n_decoys:,}')
        else:
            logging.warning(f'no decoy column was found')

        if 'elution_group_idx' in self.speclib.precursor_df.columns:
            n_elution_groups = len(self.speclib.precursor_df['elution_group_idx'].unique())
            average_precursors_per_group = len(self.speclib.precursor_df)/n_elution_groups
            logging.info(f'Number of elution groups: {n_elution_groups:,}')
            logging.info(f'\taverage size: {average_precursors_per_group:.2f}')

        else:
            logging.warning(f'no elution_group_idx column was found')

        if 'proteins' in self.speclib.precursor_df.columns:
            n_proteins = len(self.speclib.precursor_df['proteins'].unique())
            logging.info(f'Number of proteins: {n_proteins:,}')
        else:
            logging.warning(f'no proteins column was found')

        if 'isotope_apex_offset' in self.speclib.precursor_df.columns:
            logging.info(f'Isotope_apex_offset column found')
        else:
            logging.warning(f'No isotope_apex_offset column was found')
        
        logging.info(f'=================================')


        
    def get_rt_type(self, speclib):
        """check the retention time type of a spectral library
    

        Parameters
        ----------
        speclib : SpecLibBase
            spectral library

        Returns
        -------
        str
            retention time type, possible options: 'unknown','seconds', 'minutes', 'norm', 'irt'
        
        """

        rt_type = 'unknown'

        rt_series = speclib.precursor_df['rt_library']

        if rt_series.min() < 0:
            rt_type = 'irt'
        
        elif 0 <= rt_series.min() <= 1:
            rt_type = 'norm'

        elif rt_series.max() < self.config['extraction']['rt_heuristic']:
            rt_type = 'minutes'

        elif rt_series.max() > self.config['extraction']['rt_heuristic']:
            rt_type = 'seconds'

        if rt_type == 'unknown':
            logging.warning("""Could not determine retention time typ. 
                            Raw values will be used. 
                            Please specify extraction.rt_type with the possible values ('irt', 'norm, 'minutes', 'seconds',) in the config file.""")

        return rt_type
    

    def rename_columns(self, dataframe, group):
        logging.info(f'renaming {group} columns')
        # precursor columns
        if group in self.config['extraction']:
            for key, value in self.config['extraction'][group].items():
                # column which should be created already exists
                if key in dataframe.columns:
                    continue
                # column does not yet exist
                else:
                    for candidate_columns in value:
                        if candidate_columns in dataframe.columns:
                            dataframe.rename(columns={candidate_columns: key}, inplace=True)
                            # break after first match
                            break
        else:
            logging.error(f'no {group} columns specified in extraction config')

    def add_precursor_columns(self, dataframe):

        if not 'precursor_idx' in dataframe.columns:
            dataframe['precursor_idx'] = np.arange(len(dataframe))
            logging.warning(f'no precursor_idx column found, creating one')

        if not 'elution_group_idx' in dataframe.columns:
            dataframe['elution_group_idx'] = self.get_elution_group_idx(dataframe, strategy='precursor')
            logging.warning(f'no elution_group_idx column found, creating one')

    def get_elution_group_idx(self, dataframe, strategy='precursor'):

        if strategy == 'precursor':
            return dataframe['precursor_idx']

        else:
            raise NotImplementedError(f'elution group strategy {strategy} not implemented')

    def get_run_data(self):
        """Generator for raw data and spectral library."""
        
        # get retention time format
        if 'rt_type' in self.config:
            rt_type = self.config['rt_type']
            logging.info(f'forcing rt_type {rt_type} from config file')
        else:
            rt_type = self.get_rt_type(self.speclib)
            logging.info(f'rt_type automatically determined as {rt_type}')

        # iterate over raw files and yield raw data and spectral library
        for raw_location in self.raw_file_list:
            raw = TimsTOFDIA_(raw_location)
            raw_name = Path(raw_location).stem

            precursor_df = self.speclib.precursor_df.copy()
            precursor_df['raw_name'] = raw_name

            if rt_type == 'seconds' or rt_type == 'unknown':
                yield raw, precursor_df, self.speclib.fragment_df
            
            elif rt_type == 'minutes':
                precursor_df['rt_library'] *= 60

                yield raw, precursor_df, self.speclib.fragment_df

            elif rt_type == 'irt':
                raise NotImplementedError()
            
            elif rt_type == 'norm':
                # the normalized rt is transformed to extend from the center of the lowest to the center of the highest rt window
                rt_min = self.config['extraction']['initial_rt_tolerance']/2
                rt_max = raw.rt_max_value - (self.config['extraction']['initial_rt_tolerance']/2)

                precursor_df['rt_library'] = self.norm_to_rt(raw,precursor_df['rt_library'].values, active_gradient_start=rt_min, active_gradient_stop=rt_max) 

                yield raw, precursor_df, self.speclib.fragment_df
                
    def run(self, output_folder, log_neptune=False, neptune_tags=[]):


        dataframes = []

        for dia_data, precursors_flat, fragments_flat in self.get_run_data():

            try:
                workflow = Workflow(
                    self.config, 
                    dia_data, 
                    precursors_flat, 
                    fragments_flat, 
                    log_neptune=log_neptune,
                    neptune_tags=neptune_tags
                    )
                
                workflow.calibration()
                df = workflow.extraction()
                df = df[df['qval'] < 0.01]
                df = df[df['decoy'] == 0]
                df['run'] = raw_name
                dataframes.append(df)
            
            except Exception as e:
                logging.exception(f'=== error during extraction of {raw_name} ===')
                print(e)
                continue

        out_df = pd.concat(dataframes)
        out_df.to_csv(os.path.join(output_folder, f'alpha_psms.tsv'), sep='\t', index=False)

class Workflow:
    def __init__(
            self, 
            config, 
            dia_data, 
            precursors_flat, 
            fragments_flat,
            log_neptune=False,
            neptune_tags=[]
        ):
        self.config = config
        self.dia_data = dia_data
        self.raw_name = precursors_flat.iloc[0]['raw_name']
        self.precursors_flat = precursors_flat
        self.fragments_flat = fragments_flat


        if log_neptune:
            try:
                neptune_token = os.environ['NEPTUNE_TOKEN']
            except KeyError:
                logging.error('NEPTUNE_TOKEN environtment variable not set')
                raise KeyError from None

            self.run = neptune.init_run(
                project="MannLabs/alphaDIA",
                api_token=neptune_token
            )

            self.run['version'] = self.config['version']
            self.run["sys/tags"].add(neptune_tags)
            self.run['host'] = socket.gethostname()
            self.run['raw_file'] = self.raw_name
            self.run['config'].upload(File.from_content(yaml.dump(self.config)))
        else:
            self.run = None

    @property
    def config(
            self
        ) -> dict:
        """Dict with all configuration parameters for the extraction.
        """
        return self._config
    
    @config.setter
    def config(
            self, 
            config : dict
        ) -> None:
        self._config = config

    def get_exponential_batches(self, step):
        """Get the number of batches for a given step
        This plan has the shape:
        1, 1, 1, 2, 4, 8, 16, 32, 64, ...
        """
        return int(2 ** max(step - 3,0))

    def get_batch_plan(self):
        n_eg = self.precursors_flat['elution_group_idx'].nunique()

        plan = []

        batch_size = self.config['extraction']['batch_size']
        step = 0
        start_index = 0

        while start_index < n_eg:
            n_batches = self.get_exponential_batches(step)
            stop_index = min(start_index + n_batches * batch_size, n_eg)
            plan.append((start_index, stop_index))
            step += 1
            start_index = stop_index

        return plan

    def start_of_calibration(self):

        self.calibration_manager = RunCalibration()
        self.calibration_manager.load_config(self.config)
        self.batch_plan = self.get_batch_plan()

        # initialize the progress dict
        self.progress = {
            'current_epoch': 0,
            'current_step': 0,
            'ms1_error': self.config['extraction']['initial_ms1_tolerance'],
            'ms2_error': self.config['extraction']['initial_ms2_tolerance'],
            'rt_error': self.config['extraction']['initial_rt_tolerance'],
            'mobility_error': self.config['extraction']['initial_mobility_tolerance'],
            'column_type': 'library',
            'num_candidates': self.config['extraction']['initial_num_candidates'],
            'recalibration_target': self.config['extraction']['recalibration_target'],
            'accumulated_precursors': 0,
            'accumulated_precursors_0.01FDR': 0,
            'accumulated_precursors_0.001FDR': 0,
        }

    def start_of_epoch(self, current_epoch):
        self.progress['current_epoch'] = current_epoch

        if self.run is not None:
            self.run["eval/epoch"].log(current_epoch)

        self.elution_group_order = self.precursors_flat['elution_group_idx'].sample(frac=1).values


        self.calibration_manager.predict(self.precursors_flat, 'precursor')
        self.calibration_manager.predict(self.fragments_flat, 'fragment')

        # make updates to the progress dict depending on the epoch
        if self.progress['current_epoch'] > 0:
            self.progress['num_candidates'] = 1
            self.progress['recalibration_target'] = self.config['extraction']['recalibration_target'] * (1+current_epoch)

    def start_of_step(self, current_step, start_index, stop_index):
        self.progress['current_step'] = current_step
        if self.run is not None:
            self.run["eval/step"].log(current_step)

            for key, value in self.progress.items():
                self.run[f"eval/{key}"].log(value)

        logging.info(f'=== Epoch {self.progress["current_epoch"]}, step {current_step}, extracting elution groups {start_index} to {stop_index} ===')

    def check_epoch_conditions(self):

        continue_calibration = False

        if self.progress['ms1_error'] > self.config['extraction']['target_ms1_tolerance']:
            continue_calibration = True

        if self.progress['ms2_error'] > self.config['extraction']['target_ms2_tolerance']:
            continue_calibration = True

        if self.progress['rt_error'] > self.config['extraction']['target_rt_tolerance']:
            continue_calibration = True

        if self.progress['mobility_error'] > self.config['extraction']['target_mobility_tolerance']:
            continue_calibration = True

        return continue_calibration

    def calibration(self):
        
        self.start_of_calibration()
        for current_epoch in range(self.config['extraction']['max_epochs']):
            self.start_of_epoch(current_epoch)
        
            
            if self.check_epoch_conditions():
                pass
            else:
                break
        
            features = []
            for current_step, (start_index, stop_index) in enumerate(self.batch_plan):
                self.start_of_step(current_step, start_index, stop_index)

                eg_idxes = self.elution_group_order[start_index:stop_index]
                batch_df = self.precursors_flat[self.precursors_flat['elution_group_idx'].isin(eg_idxes)]
                
                features += [self.extract_batch(batch_df)]
                features_df = pd.concat(features)

                logging.info(f'number of dfs in features: {len(features)}, total number of features: {len(features_df)}')
                precursor_df = self.fdr_correction(features_df)

                if self.check_recalibration(precursor_df):
                    self.recalibration(precursor_df)
                    break
                else:
                    pass
            
            self.end_of_epoch()

        

        if 'final_full_calibration' in self.config['extraction']:
            if self.config['extraction']['final_full_calibration']:
                logging.info('Performing final calibration with all precursors')
                features_df = self.extract_batch(self.precursors_flat)
                precursor_df = self.fdr_correction(features_df)
                self.recalibration(precursor_df)

        self.end_of_calibration()


    def end_of_epoch(self):
        pass

    def end_of_calibration(self):
        self.calibration_manager.predict(self.precursors_flat, 'precursor')
        self.calibration_manager.predict(self.fragments_flat, 'fragment')
        pass

    def recalibration(self, precursor_df):
        precursor_df_filtered = precursor_df[precursor_df['qval'] < 0.001]
        precursor_df_filtered = precursor_df_filtered[precursor_df_filtered['decoy'] == 0]

        self.calibration_manager.fit(precursor_df_filtered,'precursor', plot=True, neptune_run=self.run)
        m1_70 = self.calibration_manager.get_estimator('precursor', 'mz').ci(precursor_df, 0.70)[0]
        m1_99 = self.calibration_manager.get_estimator('precursor', 'mz').ci(precursor_df, 0.99)[0]
        rt_70 = self.calibration_manager.get_estimator('precursor', 'rt').ci(precursor_df, 0.70)[0]
        rt_99 = self.calibration_manager.get_estimator('precursor', 'rt').ci(precursor_df, 0.99)[0]
        mobility_70 = self.calibration_manager.get_estimator('precursor', 'mobility').ci(precursor_df, 0.70)[0]
        mobility_99 = self.calibration_manager.get_estimator('precursor', 'mobility').ci(precursor_df, 0.99)[0]

        fragment_calibration_df = unpack_fragment_info(precursor_df_filtered)
        fragment_calibration_df = fragment_calibration_df.sort_values(by=['intensity'], ascending=True).head(20000)
        self.calibration_manager.fit(fragment_calibration_df,'fragment', plot=True, neptune_run=self.run)
        m2_70 = self.calibration_manager.get_estimator('fragment', 'mz').ci(precursor_df, 0.70)[0]
        m2_99 = self.calibration_manager.get_estimator('fragment', 'mz').ci(precursor_df, 0.99)[0]

        self.progress["ms1_error"] = max(m1_70, self.config['extraction']['target_ms1_tolerance'])
        self.progress["ms2_error"] = max(m2_70, self.config['extraction']['target_ms2_tolerance'])
        self.progress["rt_error"] = max(rt_70, self.config['extraction']['target_rt_tolerance'])
        self.progress["mobility_error"] = max(mobility_70, self.config['extraction']['target_mobility_tolerance'])
        self.progress["column_type"] = 'calibrated'

        if self.run is not None:
            precursor_df_fdr = precursor_df_filtered[precursor_df_filtered['qval'] < 0.01]
            self.run["eval/precursors"].log(len(precursor_df_fdr))
            self.run['eval/99_ms1_error'].log(m1_99)
            self.run['eval/99_ms2_error'].log(m2_99)
            self.run['eval/99_rt_error'].log(rt_99)
            self.run['eval/99_mobility_error'].log(mobility_99)

    
    def check_recalibration(self, precursor_df):
        self.progress['accumulated_precursors'] = len(precursor_df)
        self.progress['accumulated_precursors_0.01FDR'] = len(precursor_df[precursor_df['qval'] < 0.01])
        self.progress['accumulated_precursors_0.001FDR'] = len(precursor_df[precursor_df['qval'] < 0.001])

        logging.info(f'=== checking if recalibration conditions were reached, target {self.progress["recalibration_target"]} precursors ===')

        logging.info(f'Accumulated precursors: {self.progress["accumulated_precursors"]:,}, 0.01 FDR: {self.progress["accumulated_precursors_0.01FDR"]:,}, 0.001 FDR: {self.progress["accumulated_precursors_0.001FDR"]:,}')

        perform_recalibration = False

        if self.progress['accumulated_precursors_0.001FDR'] > self.progress['recalibration_target']:
            perform_recalibration = True
           
        if self.progress['current_step'] == len(self.batch_plan) -1:
            perform_recalibration = True

        return perform_recalibration

    
    def fdr_correction(self, features_df):
        return fdr_correction(features_df, neptune_run=self.run)
        

    def extract_batch(self, batch_df):
        logging.info(f'MS1 error: {self.progress["ms1_error"]}, MS2 error: {self.progress["ms2_error"]}, RT error: {self.progress["rt_error"]}, Mobility error: {self.progress["mobility_error"]}')

        extraction = MS1CentricCandidateSelection(
            self.dia_data,
            batch_df,
            rt_column = f'rt_{self.progress["column_type"]}',
            mobility_column = f'mobility_{self.progress["column_type"]}',
            precursor_mz_column = f'mz_{self.progress["column_type"]}',
            rt_tolerance = self.progress["rt_error"],
            mobility_tolerance = self.progress["mobility_error"],
            num_candidates = self.progress["num_candidates"],
            num_isotopes=2,
            mz_tolerance = self.progress["ms1_error"],
        )
        candidates_df = extraction()

        extraction = MS2ExtractionWorkflow(
            self.dia_data,
            batch_df,
            candidates_df,
            self.fragments_flat,
            coarse_mz_calibration = False,
            rt_column = f'rt_{self.progress["column_type"]}',
            mobility_column = f'mobility_{self.progress["column_type"]}',
            precursor_mz_column = f'mz_{self.progress["column_type"]}',
            fragment_mz_column = f'mz_{self.progress["column_type"]}',
            precursor_mass_tolerance = self.progress["ms1_error"],
            fragment_mass_tolerance = self.progress["ms2_error"],
        )
        features_df = extraction()
        features_df['decoy'] = batch_df['decoy'].values[features_df['index'].values]
        features_df['charge'] = batch_df['charge'].values[features_df['index'].values]
        features_df['nAA'] = batch_df['nAA'].values[features_df['index'].values]
        features_df['sequence'] = batch_df['sequence'].values[features_df['index'].values]
        
        features_df['index'] += batch_df.first_valid_index()
        
        return features_df
       
    def extraction(self):

        if self.run is not None:
            for key, value in self.progress.items():
                self.run[f"eval/{key}"].log(value)

        self.progress["num_candidates"] = self.config['extraction']['target_num_candidates']

        features_df = self.extract_batch(self.precursors_flat)
        precursor_df = self.fdr_correction(features_df)

        precursor_df = precursor_df[precursor_df['decoy'] == 0]
        precursors_05 = len(precursor_df[precursor_df['qval'] < 0.05])
        precursors_01 = len(precursor_df[precursor_df['qval'] < 0.01])
        precursors_001 = len(precursor_df[precursor_df['qval'] < 0.001])


        
        if self.run is not None:
            self.run["eval/precursors"].log(precursors_01)
            self.run.stop()

        logging.info(f'=== extraction finished, 0.05 FDR: {precursors_05:,}, 0.01 FDR: {precursors_01:,}, 0.001 FDR: {precursors_001:,} ===')

        return precursor_df   

"""
class ExtractionPlan():

    def __init__(self, psm_reader_name, decoy_type='diann'):
        self.psm_reader_name = psm_reader_name
        self.runs = []
        self.speclib = alphabase.spectral_library.library_base.SpecLibBase(decoy=decoy_type)

    def set_precursor_df(self, precursor_df):
        self.speclib.precursor_df = precursor_df

        logging.info('Initiate run mapping')

        # init run mapping
        for i, raw_name in enumerate(self.speclib.precursor_df['raw_name'].unique()):
            logging.info(f'run: {i} , name: {raw_name}')
            self.runs.append(
                {
                    "name": raw_name, 
                    'index': i, 
                    'path': os.path.join(self.data_path, f'{raw_name}.d')
                }
            )

        self.process_psms()

    def has_decoys(self):
        if 'decoy' in self.speclib.precursor_df.columns:
            return self.speclib.precursor_df['decoy'].sum() > 0
        else:
            return False

    def process_psms(self):

        # rename columns
        # all columns are expected to be observed values
        self.speclib._precursor_df.rename(
            columns={
                "rt": "rt_observed", 
                "mobility": "mobility_observed",
                "mz": "mz_observed",
                "precursor_mz": "mz_predicted",
                }, inplace=True
        )

        if not self.has_decoys():
            logging.info('no decoys were found, decoys will be generated using alphaPeptDeep')
            self.speclib.append_decoy_sequence()
            self.speclib._precursor_df.drop(['mz_predicted'],axis=1, inplace=True)
            self.speclib._precursor_df = alphabase.peptide.precursor.update_precursor_mz(self.speclib._precursor_df)
            self.speclib._precursor_df.rename(columns={"precursor_mz": "mz_predicted",}, inplace=True )

        model_mgr = peptdeep.pretrained_models.ModelManager()
        model_mgr.nce = 30
        model_mgr.instrument = 'timsTOF'

        # check if retention times are in seconds, convert to seconds if necessary
        RT_HEURISTIC = 180
        if self.speclib._precursor_df['rt_observed'].max() < RT_HEURISTIC:
            logging.info('retention times are most likely in minutes, will be converted to seconds')
            self.speclib._precursor_df['rt_observed'] *= 60

        #if not 'mz_predicted' in self.speclib._precursor_df.columns:
        #    logging.info('precursor mz column not found, column is being generated')
        #    self.speclib._precursor_df = alphabase.peptide.precursor.update_precursor_mz(self.speclib._precursor_df)
            

        if not 'rt_predicted' in self.speclib._precursor_df.columns:
            logging.info('rt prediction not found, column is being generated using alphaPeptDeep')
            self.speclib._precursor_df = model_mgr.predict_all(
                self.speclib._precursor_df,
                predict_items=['rt']
            )['precursor_df']
        
        self.speclib._precursor_df.drop(['rt_norm','rt_norm_pred'],axis=1, inplace=True)
        self.speclib.precursor_df.rename(
            columns={
                "rt_pred": "rt_predicted",
                }, inplace=True
        )
            

        if not 'mobility_pred' in self.speclib._precursor_df.columns:
            logging.info('mobility prediction not found, column is being generated using alphaPeptDeep')
            self.speclib._precursor_df = model_mgr.predict_all(
                self.speclib._precursor_df,
                predict_items=['mobility']
            )['precursor_df']

        self.speclib._precursor_df.drop(['ccs_pred','ccs'],axis=1, inplace=True)
        self.speclib.precursor_df.rename(
            columns={
                "mobility_pred": "mobility_predicted",
                }, inplace=True
        )

        self.speclib._precursor_df.drop(['precursor_mz'],axis=1, inplace=True)

    def get_calibration_df(self):
        # Used by the calibration class to get the first set of precursors used for calibration.
        # Returns a filtered subset of the precursor_df based on metrics like the q-value, target channel etc.
        
        calibration_df = self.speclib.precursor_df.copy()
        calibration_df = calibration_df[calibration_df['fdr'] < 0.01]
        calibration_df = calibration_df[calibration_df['decoy'] == 0]

        return calibration_df

    def validate(self):
        #Validate extraction plan before proceeding
        

        logging.info('Validating extraction plan')

        if not hasattr(self,'precursor_df'):
            logging.error('No precursor_df found')
            return

        if not hasattr(self,'fragment_mz_df'):
            logging.error('No fragment_mz_df found')

        if not hasattr(self,'fragment_intensity_df'):
            logging.error('No fragment_intensity_df found')

        # check if all mandatory columns were found
        mandatory_precursor_df_columns = ['raw_name', 
                            'decoy',
                            'charge',
                            'frag_start_idx',
                            'frag_end_idx',
                            'precursor_mz',
                            'rt_pred',
                            'mobility_pred',
                            'mz_values',
                            'rt_values',
                            'mobility_values',
                            'fdr']

        for item in mandatory_precursor_df_columns:
            if not item in self.precursor_df.columns.to_list():
                logging.error(f'The mandatory column {item} was missing from the precursor_df')

        logging.info('Extraction plan succesfully validated')

    def set_library(self, lib: peptdeep.protein.fasta.FastaLib):
        self.lib = lib

    def set_data_path(self, folder):
        self.data_path = folder

    def set_calibration(self, estimators):
        
        self.calibration = alphadia.extraction.calibration.GlobalCalibration(self)
        self.calibration.set_estimators(estimators)

    def add_normalized_properties(self):

        # initialize normalized properties with zeros
        for property in self.calibration.prediction_targets:
            self.speclib._precursor_df[f'{property}_norm'] = 0

            for i, run in enumerate(self.runs):
                run_mask = self.speclib.precursor_df['raw_name'] == run['name']
                run_speclib = self.speclib.precursor_df[run_mask]
                
                # predicted value like rt_pred or mobility_pred
                source_column = self.calibration.prediction_targets[property][0]
                # measured value like rt or mobility
                target_column = self.calibration.prediction_targets[property][1]

                target = run_speclib[target_column].values
                source = run_speclib[source_column].values
                source_calibrated = self.calibration.predict(i, property, source)
                target_deviation = target / source_calibrated

                self.speclib._precursor_df.loc[run_mask, f'{property}_norm'] = target_deviation

            # make sure there are no zero values
            zero_vals = np.sum(self.speclib._precursor_df[f'{property}_norm'] == 0)
            if zero_vals > 0:
                logging.warning(f'normalisied property {property} has not been set for {zero_vals} entries')

        for run in self.runs:
            run_speclib = self.speclib.precursor_df[self.speclib.precursor_df['raw_name'] == run['name']]

            pass

    def build_run_precursor_df(self, run_index):
        
        #build run specific speclib which combines entries from other runs
        

        self.speclib.hash_precursor_df()

        # IDs from the own run are already calibrated
        run_name = self.runs[run_index]['name']
        run_precursor_df = self.speclib.precursor_df[self.speclib.precursor_df['raw_name'] == run_name].copy()
        run_precursor_df['same_run'] = 1
        existing_precursors = run_precursor_df['mod_seq_charge_hash'].values

        # assemble IDs from other runs
        other_speclib = self.speclib.precursor_df[self.speclib.precursor_df['raw_name'] != run_name]
        other_speclib = other_speclib[~other_speclib['mod_seq_charge_hash'].isin(existing_precursors)]

        # TODO sloooooow, needs to be optimized
        extra_precursors = []
        grouped = other_speclib.groupby('mod_seq_charge_hash')
        for name, group in grouped:
            group_dict = group.to_dict('records')

            out_dict = group_dict[0]
            for property in self.calibration.prediction_targets:
                out_dict[f'{property}_norm'] = group[f'{property}_norm'].median()

            extra_precursors.append(out_dict)

        nonrun_precursor_df = pd.DataFrame(extra_precursors)
        nonrun_precursor_df['same_run'] = 0
        new_precursor_df = pd.concat([run_precursor_df, nonrun_precursor_df]).reset_index(drop=True)

        # apply run specific calibration function
        for property, columns in self.calibration.prediction_targets.items():

            source_column = columns[0]
            target_column = columns[1]
            
            new_precursor_df[target_column] = self.calibration.predict(run_index,property,new_precursor_df[source_column].values)*new_precursor_df[f'{property}_norm']

        # flatten out the mz_values and intensity_values
            
        # flatten precursor
        precursors_flat, fragments_flat = alphabase.peptide.fragment.flatten_fragments(
            new_precursor_df,
            self.speclib.fragment_mz_df,
            self.speclib.fragment_intensity_df,
            intensity_treshold = 0
        )

        fragments_flat.rename(
            columns={
                "mz": "mz_predicted",
                }, inplace=True
        )

        if 'precursor_mz' in self.calibration.estimators[run_index].keys():
            logging.info('Performing precursor_mz calibration')
            source_column, target_column = self.calibration.precursor_calibration_targets['precursor_mz']
            precursors_flat[target_column] = self.calibration.predict(run_index, 'precursor_mz', precursors_flat[source_column].values)    
        else:
            logging.info('No precursor_mz calibration found, using predicted values')

        if 'fragment_mz' in self.calibration.estimators[run_index].keys():
            logging.info('Performing fragment_mz calibration')
            source_column, target_column = self.calibration.fragment_calibration_targets['fragment_mz']
            fragments_flat[target_column] = self.calibration.predict(run_index, 'fragment_mz', fragments_flat[source_column].values)    
        else:
            logging.info('No fragment_mz calibration found, using predicted values')

        return precursors_flat, fragments_flat


class LibraryManager():

    def __init__(self, decoy_type='diann'):
        self.runs = []
        self.speclib = alphabase.spectral_library.library_base.SpecLibBase(decoy=decoy_type)

    def set_precursor_df(self, precursor_df):
        self.speclib.precursor_df = precursor_df

        logging.info('Initiate run mapping')

        # init run mapping
        for i, raw_name in enumerate(self.speclib.precursor_df['raw_name'].unique()):
            logging.info(f'run: {i} , name: {raw_name}')
            self.runs.append(
                {
                    "name": raw_name, 
                    'index': i, 
                    'path': os.path.join(self.data_path, f'{raw_name}.d')
                }
            )

        self.process_psms()

    def has_decoys(self):
        if 'decoy' in self.speclib.precursor_df.columns:
            return self.speclib.precursor_df['decoy'].sum() > 0
        else:
            return False

    def process_psms(self):

        # rename columns
        # all columns are expected to be observed values
        self.speclib._precursor_df.rename(
            columns={
                "rt": "rt_library", 
                "mobility": "mobility_library",
                "mz": "mz_library",
                "precursor_mz": "mz_library",
                }, inplace=True
        )

        if not self.has_decoys():
            logging.info('no decoys were found, decoys will be generated using alphaPeptDeep')
            self.speclib.append_decoy_sequence()
            self.speclib._precursor_df.drop(['mz_library'],axis=1, inplace=True)
            self.speclib._precursor_df = alphabase.peptide.precursor.update_precursor_mz(self.speclib._precursor_df)
            self.speclib._precursor_df.rename(columns={"precursor_mz": "mz_library",}, inplace=True )

        # check if retention times are in seconds, convert to seconds if necessary
        RT_HEURISTIC = 180
        if self.speclib._precursor_df['rt_library'].max() < RT_HEURISTIC:
            logging.info('retention times are most likely in minutes, will be converted to seconds')
            self.speclib._precursor_df['rt_library'] *= 60

        #if not 'mz_predicted' in self.speclib._precursor_df.columns:
        #    logging.info('precursor mz column not found, column is being generated')
        #    self.speclib._precursor_df = alphabase.peptide.precursor.update_precursor_mz(self.speclib._precursor_df)
        if 'precursor_mz' in self.speclib._precursor_df.columns:
            self.speclib._precursor_df.drop(['precursor_mz'],axis=1, inplace=True)


    def set_library(self, lib: peptdeep.protein.fasta.FastaLib):
        self.lib = lib

    def set_data_path(self, folder):
        self.data_path = folder

    def build_run_precursor_df(self, run_index):
        
        # build run specific speclib which combines entries from other runs
        

        self.speclib.hash_precursor_df()

        # IDs from the own run are already calibrated
        run_name = self.runs[run_index]['name']
        run_precursor_df = self.speclib.precursor_df[self.speclib.precursor_df['raw_name'] == run_name].copy()
             
        # flatten precursor
        precursors_flat, fragments_flat = alphabase.peptide.fragment.flatten_fragments(
            run_precursor_df,
            self.speclib.fragment_mz_df,
            self.speclib.fragment_intensity_df,
            intensity_treshold = 0
        )

        fragments_flat.rename(
            columns={
                "mz": "mz_library"
                }, inplace=True
        )


        return precursors_flat, fragments_flat
"""