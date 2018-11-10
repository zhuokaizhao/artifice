"""Implements artifice's detection scheme from end to end.

Training scheme:
* load a dataset, which may or may not be labeled with full annotations.
* For each batch, obtain the semantic annotations from labeller.py. (This may
  involve just taking the first num_classes channels of full_annotation.)
* Perform any augumentations using that newly-labelled data. Add all of this to
  the dataset, which should be rewritten? Unclear how to keep track of a
  constantly changing dataset.
* Train the semantic segmentation on this batch, using semantic_model.py.
* 

"""

import tensorflow as tf


"""Artifice's full detection scheme
"""
class Model:
  def __init__(self):
    pass
