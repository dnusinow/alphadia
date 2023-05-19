import pandas as pd
import logging
import numpy as np

logger = logging.getLogger()

class Property():
    def __init__(self, name, type):
        """
        Base class for all properties

        Parameters
        ----------

        name: str
            Name of the property

        type: type
            Type of the property

        """
        self.name = name
        self.type = type

class Optional(Property):
    def __init__(self, name, type):
        """
        Optional property
        
        Parameters
        ----------
        
        name: str
            Name of the property
                
        type: type
            Type of the property
            
        """

        self.name = name
        self.type = type

    def __call__(self, df, logging = True):
        """
        Casts the property to the specified type if it is present in the dataframe

        Parameters
        ----------

        df: pd.DataFrame
            Dataframe to validate

        logging: bool
            If True, log the validation results       
        """

        if self.name in df.columns:
            if df[self.name].dtype != self.type:
                df[self.name] = df[self.name].astype(self.type)

        return True

class Required(Property):
    def __init__(self, name, type):
        """
        Required property

        Parameters
        ----------

        name: str
            Name of the property

        type: type
            Type of the property

        """
        self.name = name
        self.type = type

    def __call__(self, df, logging = True):
        """
        Casts the property to the specified type if it is present in the dataframe

        Parameters
        ----------

        df: pd.DataFrame
            Dataframe to validate

        logging: bool
            If True, log the validation results

        """

        if self.name in df.columns:       
            if df[self.name].dtype != self.type:
                df[self.name] = df[self.name].astype(self.type)

            return True
        else:
            return False

class Schema():
    def __init__(self, name, properties):
        """
        Schema for validating dataframes

        Parameters
        ----------

        name: str
            Name of the schema

        properties: list
            List of Property objects

        """

        self.name = name
        self.schema = properties
        for property in self.schema:
            if not isinstance(property, Property):
                raise ValueError(f"Schema must contain only Property objects")

    def __call__(self, df, logging = True):
        """
        Validates the dataframe

        Parameters
        ----------

        df: pd.DataFrame
            Dataframe to validate

        logging: bool
            If True, log the validation results

        """

        for property in self.schema:
            if not property(df, logging = logging):
                raise ValueError(f"Validation of {self.name} failed: Column {property.name} is not present in the dataframe")

            
precursors_flat = Schema(
    "precursors_flat",
    [
        Required('elution_group_idx', np.uint32),
        Required('score_group_idx', np.uint32),
        Required('precursor_idx', np.uint32),
        Required('channel', np.uint32),
        Required('decoy', np.uint8),
        Required('flat_frag_start_idx', np.uint32),
        Required('flat_frag_stop_idx', np.uint32),
        Required('charge', np.uint8),
        Required('rt_library', np.float32),
        Optional('rt_calibrated', np.float32),
        Required('mobility_library', np.float32),
        Optional('mobility_calibrated', np.float32),
        Required('mz_library', np.float32),
        Optional('mz_calibrated', np.float32),
        *[Optional(f'i_{i}', np.float32) for i in range(10)]
    ]
)

fragments_flat = Schema(
    "fragments_flat",
    [
        Required('mz_library', np.float32),
        Optional('mz_calibrated', np.float32),
        Required('intensity', np.float32),
        Required('cardinality', np.uint8),
        Required('type', np.uint8),
        Required('loss_type', np.uint8),
        Required('charge', np.uint8),
        Required('number', np.uint8),
        Required('position', np.uint8)
    ]
)

candidates = Schema(
    "precursors_flat",
    [
        Required('elution_group_idx', np.uint32),
        Optional('score_group_idx', np.uint32),
        Required('precursor_idx', np.uint32),
        Required('rank', np.uint8),
        Optional('channel', np.uint8),
        Required('decoy', np.uint8),
        Required('flat_frag_start_idx', np.uint32),
        Required('flat_frag_stop_idx', np.uint32),
        Required('scan_start', np.int64),
        Required('scan_stop', np.int64),
        Required('scan_center', np.int64),
        Required('frame_start', np.int64),
        Required('frame_stop', np.int64),
        Required('frame_center', np.int64),
    ]
)


def check_critical_values(input_df):

    for col in input_df.columns:
        nan_count = input_df[col].isna().sum()
        inf_count = np.isinf(input_df[col]).sum()

        if nan_count > 0:
            nan_percentage = nan_count / len(input_df) * 100
            logger.warning(f'{col} has {nan_count} NaNs ( {nan_percentage:.2f} % out of {len(input_df)})')

        if inf_count > 0:
            inf_percentage = inf_count / len(input_df) * 100
            logger.warning(f'{col} has {inf_count} Infs ( {inf_percentage:.2f} % out of {len(input_df)})')