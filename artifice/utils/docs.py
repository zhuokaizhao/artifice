"""Documentation strings for artifice.
"""

description = """Artifice."""

command_choices = ['experiment', 'predict', 'augment']
command_help = "Artifice command to run."

data_dir_help = """Data input directory. Initially, this can take the shape of
an "images" directory and a "labels.npy" file with associated labels. This gets
converted "data.tfrecord" before anything else, which has the associated
(image, label) pairs."""

output_help = """Output."""

model_dir_help = """Model directory. Contains "model/" with the actual model
checkpoints and associated files/plots."""

overwrite_help = """overwrite existing model; restart training from scratch."""

image_shape_help = """Shape of the image. Must be 3D. Grayscale uses 1 for last
dimension."""

tile_shape_help = """Shape of the tiles for training. Must be 3D."""

epochs_help = """Number of training epochs. Default is 1."""

batch_size_help = """Batch size in untiled images. (Actual batch size is batch_size *
num_tiles)."""

learning_rate_help = """Learning rate."""

num_objects_help = """Maximum number of objects."""

splits_help = """Splits to use for unlabeled, validation, and testing."""

epoch_size_help = """Number of artificial examples per training epoch."""

subset_size_help = """Number of examples from the unlabeled set to label or
annotate."""

num_candidates_help = """Number of unlabeled examples to consider at once for 
annotation."""

query_size_help = """Number of examples to query at once."""

l2_reg_help = """Not used."""

cores_help = """Number of CPU cores to parallelize over. Default (-1) uses
available cores."""

verbose_help = """Artifice verbosity. Default is 2 (debug level)."""

keras_verbose_help = """Keras verbosity. Default is 1 (progress bars)."""

eager_help = """Enable eager execution."""

show_help = """Show plots rather than save them."""

mode_help = """Training mode."""

regions_help = """Regions numpy file path."""
