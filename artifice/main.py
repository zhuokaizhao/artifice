"""The main script for running artifice.

"""

import os
from os.path import join, exists
import sys
from time import time, asctime
from glob import glob
import argparse
import numpy as np

import tensorflow as tf

from artifice import log
from artifice.log import logger
from artifice import dat
from artifice import mod
from artifice import docs
from artifice import vis
from artifice import conversions
from artifice import utils
from artifice import ann
from artifice import prio
from artifice import tform



def _system_checks():
  if sys.version_info < (3, 6):
    logger.error("Required: Python3.6 or higher.")
    exit()
  if tuple(map(int, tf.__version__.split('.'))) < (1, 13, 1):
    logger.error("Required: TensorFlow 1.13.1 or higher.")
    exit()


def _set_eager(eager):
  if eager:
    tf.enable_eager_execution()


def _ensure_dirs_exist(dirs):
  for path in dirs:
    if not exists(path):
      logger.info(f"creating '{path}'")
      os.makedirs(path)


class Artifice:
    """Bag of state or Main() class that directs a single `artifice` run.

    All arguments are required keyword arguments, for the sake of
    correctness. Defaults are specified in the command-line defaults for this
    script. Run `python artifice.py -h` for more info.

    # todo: copy docs here

    """

    def __init__(self, *,  # pylint: disable=too-many-statements
                commands,
                data_root,
                model_root,
                overwrite,
                deep,
                figs_dir,
                convert_mode,
                transformation,
                identity_prob,
                priority_mode,
                labeled,
                annotation_mode,
                record_size,
                annotation_delay,
                image_shape,
                data_size,
                test_size,
                batch_size,
                num_objects,
                pose_dim,
                num_shuffle,
                base_shape,
                level_filters,
                level_depth,
                model,
                multiscale,
                use_var,
                dropout,
                initial_epoch,
                epochs,
                learning_rate,
                tol,
                num_parallel_calls,
                verbose,
                keras_verbose,
                eager,
                show,
                cache,
                seconds,
                vis_number):
        # main
        self.commands = commands

        # file settings
        self.data_root = data_root
        self.model_root = model_root
        self.overwrite = overwrite
        self.deep = deep
        self.figs_dir = figs_dir

        # data settings
        self.convert_modes = utils.listwrap(convert_mode)
        self.transformation = transformation
        self.identity_prob = identity_prob
        self.priority_mode = priority_mode
        self.labeled = labeled

        # annotation settings
        self.annotation_mode = annotation_mode
        self.record_size = record_size
        self.annotation_delay = annotation_delay

        # data sizes/settings
        self.image_shape = image_shape
        self.data_size = data_size
        self.test_size = test_size
        self.batch_size = batch_size
        self.num_objects = num_objects
        self.pose_dim = pose_dim
        self.num_shuffle = num_shuffle

        # model architecture
        self.base_shape = utils.listify(base_shape, 2)
        self.level_filters = level_filters
        self.level_depth = level_depth

        # model type settings
        self.model = model
        self.multiscale = multiscale
        self.use_var = use_var

        # hyperparameters
        self.dropout = dropout
        self.initial_epoch = initial_epoch
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.tol = tol

        # runtime settings
        self.num_parallel_calls = num_parallel_calls
        self.verbose = verbose
        self.keras_verbose = keras_verbose
        self.eager = eager
        self.show = show
        self.cache = cache
        self.seconds = seconds

        # globals
        log.set_verbosity(self.verbose)
        _set_eager(self.eager)
        vis.set_show(self.show)
        self._set_num_parallel_calls()

        # derived sizes/shapes
        self.num_levels = len(self.level_filters)
        self.input_tile_shape = mod.UNet.compute_input_tile_shape_(
            self.base_shape, self.num_levels, self.level_depth)
        self.output_tile_shapes = mod.UNet.compute_output_tile_shapes_(
            self.base_shape, self.num_levels, self.level_depth)
        self.output_tile_shape = self.output_tile_shapes[-1]
        self.num_tiles = dat.ArtificeData.compute_num_tiles(
            self.image_shape, self.output_tile_shape)

        # derived model subdirs/paths
        self.cache_dir = join(self.model_root, 'cache')
        self.annotation_info_path = join(self.model_root, 'annotation_info.pkl')
        self.annotated_dir = join(self.model_root, 'annotated')  # model-dependent

        # ensure directories exist
        _ensure_dirs_exist([self.data_root, self.model_root, self.figs_dir,
                            self.cache_dir, self.annotated_dir])

        # visualizing testing results
        self.vis_number = vis_number

    """
    Helper functions.
    """

    def __str__(self):
        return f"""{asctime()}:
                data_root: {self.data_root}
                model_root: {self.model_root}
                figs_dir: {self.figs_dir}
                ----
                labeled: {self.labeled}
                num_parallel_calls: {self.num_parallel_calls}
                ----
                input tile shape: {self.input_tile_shape}
                output shapes: {self.output_tile_shapes}
                todo: other attributes"""

    def __call__(self):
        for command in self.commands:
            if (command[0] == '_'
                or not hasattr(self, command)
                or not callable(getattr(self, command))):
                raise RuntimeError(f"bad command: {command}")
            getattr(self, command)()

    def _set_num_parallel_calls(self):
        if self.num_parallel_calls <= 0:
            self.num_parallel_calls = os.cpu_count()

    """
    Loading datasets and models.
    """

    @property
    def _data_kwargs(self):
        return {'image_shape': self.image_shape,
                'input_tile_shape': self.input_tile_shape,
                'output_tile_shapes': self.output_tile_shapes,
                'batch_size': self.batch_size,
                'num_parallel_calls': self.num_parallel_calls,
                'num_shuffle': min(self.data_size, self.num_shuffle),
                'cache_dir': self.cache_dir}

    def _load_labeled(self):
        return dat.LabeledData(join(self.data_root, 'labeled_set.tfrecord'),
                                size=self.data_size, **self._data_kwargs)

    def _load_unlabeled(self):
        return dat.UnlabeledData(join(self.data_root, 'unlabeled_set.tfrecord'),
                                    size=self.data_size, **self._data_kwargs)

    def _load_annotated(self):
        transformation = (None if self.transformation is None else
                            tform.transformations[self.transformation])
        return dat.AnnotatedData(self.annotated_dir, transformation=transformation,
                                    size=self.data_size, **self._data_kwargs)

    def _load_test(self):
        return dat.LabeledData(join(self.data_root, 'test_set.tfrecord'),
                                size=self.test_size, **self._data_kwargs)

    def _load_train(self):
        if self.labeled:
            return self._load_labeled()
        return self._load_annotated()

    @property
    def _model_kwargs(self):
        kwargs = {'base_shape': self.base_shape,
                'level_filters': self.level_filters,
                'num_channels': self.image_shape[2],
                'pose_dim': self.pose_dim,
                'level_depth': self.level_depth,
                'dropout': self.dropout,
                'model_dir': self.model_root,
                'learning_rate': self.learning_rate,
                'overwrite': self.overwrite}
        if (self.use_var and self.model == 'sparse'
            or self.model == 'better-sparse'
            or self.model == 'auto-sparse'):
            kwargs['batch_size'] = self.batch_size

        if 'sparse' in self.model:
            kwargs['tol'] = self.tol

        return kwargs

    def _load_model(self):
        kwargs = self._model_kwargs
        if self.model == 'unet':
            return mod.UNet(**kwargs)
        elif self.model == 'sparse':
            return mod.SparseUNet(**kwargs)
        elif self.model == 'better-sparse':
            return mod.BetterSparseUNet(**kwargs)
        elif self.model == 'auto-sparse':
            return mod.AutoSparseUNet(**kwargs)
        else:
            raise RuntimeError(f"No '{self.model}' model type.")

    """
    Methods implementing Commands.
    """

    def convert(self):
        for mode in self.convert_modes:
            conversions.conversions[mode](
                self.data_root, test_size=self.test_size)

    def uncache(self):
        """Clean up the cache files."""
        for path in glob(join(self.model_root, "cache*")):
            utils.rm(path)

    def clean(self):
        """Clean up the files associated with this model for a future run.

        Removes the annotation info file and lock, annotation records, and
        cache.

        If --deep is specified, also removes the saved model and checkpoints. Does
        not remove data.

        """
        if self.deep:
            utils.rm(self.model_root)
        else:
            utils.rm(self.annotation_info_path)
            utils.rm(self.annotation_info_path + '.lockfile')
            utils.rm(self.annotated_dir)

    def prioritize(self):
        """Prioritize images for annotation using an active learning or other strategy.

        Note that this does not perform any labeling. It simply maintains a queue
        of the indices for examples most recently desired for labeling. This queue
        contains no repeats. The queue is saved to disk, and a file lock should be
        created whenever it is altered, ensuring that the annotator does not make a
        bad access.

        """
        kwargs = {'info_path': self.annotation_info_path}
        if self.priority_mode == 'random':
            prioritizer = prio.RandomPrioritizer(self._load_unlabeled(), **kwargs)
        elif self.priority_mode == 'uncertainty':
            prioritizer = prio.ModelUncertaintyPrioritizer(
                self._load_unlabeled(), model=self._load_model(), **kwargs)
        else:
            raise NotImplementedError(f"{self.priority_mode} priority mode")

        prioritizer.run(seconds=self.seconds)

    def annotate(self):
        """Continually annotate new examples.

        Continually access the selection queue, pop off the most recent, and
        annotate it, either with a human annotator, or automatically using prepared
        labels (and a sleep timer). Needs to keep a list of examples already
        annotated, since they will be strewn throughout different files, as well as
        respect the file lock on the queue.

        """
        kwargs = {'info_path': self.annotation_info_path,
                'annotated_dir': self.annotated_dir,
                'record_size': self.record_size}
        if self.annotation_mode == 'disks':
            annotator = ann.DiskAnnotator(self._load_labeled(),
                                            annotation_delay=self.annotation_delay,
                                            **kwargs)
        else:
            raise NotImplementedError(f"{self.annotation_mode} annotation mode")

        annotator.run(seconds=self.seconds)

    def train(self):
        """Train the model using augmented examples from the annotated set."""
        train_set = self._load_train()
        model = self._load_model()
        model.train(train_set, epochs=self.epochs,
                    initial_epoch=self.initial_epoch,
                    verbose=self.keras_verbose,
                    seconds=self.seconds,
                    cache=self.cache)

    def predict(self):
        """Run prediction on the unlabeled set."""
        unlabeled_set = self._load_unlabeled()
        model = self._load_model()
        start_time = time()
        predictions = list(model.predict(
            unlabeled_set, multiscale=self.multiscale))
        logger.info(f"ran prediction in {time() - start_time}s.")
        logger.debug(f"prediction:\n{predictions}")

        fname = join(self.model_root, 'predictions.npy')
        np.save(fname, predictions)
        logger.info(f"saved {len(predictions)} predictions to {fname}.")

    def evaluate(self):
        test_set = self._load_test()
        model = self._load_model()
        errors, num_failed = model.evaluate(test_set)
        if not errors:
            logger.warning(f"found ZERO objects, num_failed: {num_failed}")
            return

        avg_error = errors.mean(axis=0)
        total_num_objects = self.test_size * self.num_objects
        num_detected = total_num_objects - num_failed
        logger.info(f"objects detected: {num_detected} / "
                    f"{total_num_objects}")
        logger.info(f"avg (euclidean) detection error: {avg_error[0]}")
        logger.info(f"avg (absolute) pose error: {avg_error[1:]}")
        logger.info(
            "note: some objects may be occluded, making detection impossible")
        logger.info(f"avg: {errors.mean(axis=0)}")
        logger.info(f"std: {errors.std(axis=0)}")
        logger.info(f"min: {errors.min(axis=0)}")
        logger.info(f"max: {errors.max(axis=0)}")

    def vis_train(self):
        """Visualize the training set. (Mostly for debugging.)"""
        train_set = self._load_train()
        for batch in train_set.training_input():
            for b in range(self.batch_size):
                image = batch[0][b]
                targets = batch[1]
                pose = targets[0][b]
                vis.plot_image(image, None, None,
                            pose[:, :, 1], pose[:, :, 2], None,
                            targets[1][b], targets[2][b], targets[3][b],
                            columns=3)
                vis.show()

    def vis_history(self):
        # todo: fix this for multiple models in the same model_dir
        # history_files = glob(join(self.model_root, '*history.json'))
        # hists = dict((fname, utils.json_load(fname)) for fname in history_files)
        vis.plot_hists_from_dir(self.model_root)
        vis.show(join(self.figs_dir, 'history.pdf'))

    def vis_predict(self):
        """Run prediction on the test set and visualize the output."""
        test_set = self._load_test()
        model = self._load_model()
        for image, dist_image, prediction in model.predict_visualization(test_set):
            fig, axes = vis.plot_image(image, image, dist_image, colorbar=True)
            axes[0, 1].plot(prediction[:, 1], prediction[:, 0], 'rx')
            axes[0, 2].plot(prediction[:, 1], prediction[:, 0], 'rx')
            logger.info(f"prediction:\n{prediction}")
            vis.show(join(self.figs_dir, 'prediction.pdf'))
            if not self.show:
                break

    # similar to vis_predict, but now only save proxy images
    def vis_proxy(self):
        # load the test sample and model
        test_set = self._load_test()
        model = self._load_model()
        for image, dist_image, prediction in model.predict_visualization(test_set):
            fig, axes = vis.plot_image(image, image, dist_image, colorbar=True)
            vis.show('proxy.pdf', join(self.figs_dir, 'proxy.pdf'))
            if not self.show:
                break

    # get the testing result images in raster format
    def vis_test_raster(self):
        test_set = self._load_test()
        model = self._load_model()

        all_images = []
        all_dist_images = []
        all_predictions = []
        all_size_images = []
        all_shape_images = []
        test_size = self.test_size
        vis_number = self.vis_number[0]
        print('There are', test_size, 'images in the test data set')
        if (vis_number > test_size):
            print('Requested visualization number is out of range')
            return

        i = 0
        for image, dist_image, prediction, size_image, shape_image in model.predict_visualization(test_set):
            print('Testing on', i)
            all_images.append(image)
            all_dist_images.append(dist_image)
            all_predictions.append(prediction)
            all_size_images.append(size_image)
            all_shape_images.append(shape_image)
            if i == vis_number:
                break
            i += 1

        x_pixels = all_images[vis_number][vis_number].shape[0]
        y_pixels = all_images[vis_number][vis_number].shape[1]
        # save image
        image_name = 'figs/image_' + str(vis_number) + '.tiff'
        image_name_txt = 'figs/image_' + str(vis_number) + '.txt'
        vis.save_raster(all_images[vis_number], image_name)
        np.savetxt(image_name_txt, all_images[vis_number].reshape(x_pixels, y_pixels), fmt='%d')

        # save distance image
        dist_name = 'figs/dist_image_' + str(vis_number) + '.tiff'
        dist_name_txt = 'figs/dist_image_' + str(vis_number) + '.txt'
        vis.save_raster(all_dist_images[vis_number], dist_name)
        np.savetxt(dist_name_txt, all_dist_images[vis_number].reshape(x_pixels, y_pixels), fmt='%d')

        # save size image
        size_name = 'figs/size_image_' + str(vis_number) +'.tiff'
        size_name_txt = 'figs/size_image_' + str(vis_number) +'.txt'
        vis.save_raster(all_size_images[vis_number], size_name)
        np.savetxt(size_name_txt, all_size_images[vis_number].reshape(x_pixels, y_pixels), fmt='%d')

        # save shape image
        shape_name = 'figs/shape_image_' + str(vis_number) +'.tiff'
        shape_name_txt = 'figs/shape_image_' + str(vis_number) +'.txt'
        vis.save_raster(all_shape_images[vis_number], shape_name)
        np.savetxt(shape_name_txt, all_shape_images[vis_number].reshape(x_pixels, y_pixels), fmt='%d')

    def vis_outputs(self):
        """Run prediction on the test set and visualize the output."""
        test_set = self._load_test()
        model = self._load_model()
        for image, outputs in model.predict_outputs(test_set):
            pose_image = outputs[0]
            level_outputs = outputs[1:]
            columns = max(model.num_levels, model.pose_dim + 1)
            fig, axes = vis.plot_image(
                image, *[None for _ in range(columns - 1)],
                *[pose_image[:, :, i] for i in range(model.pose_dim + 1)],
                *[None for _ in range(columns - model.pose_dim - 1)],
                *level_outputs,
                *[None for _ in range(columns - model.num_levels)],
                colorbar=True, columns=columns)
            vis.show(join(self.figs_dir, 'model_outputs.pdf'))
            if not self.show:
                break


def main():
    _system_checks()

    parser = argparse.ArgumentParser(description=docs.description)
    parser.add_argument('commands', nargs='+', help=docs.commands)

    # file settings
    parser.add_argument('--data-root', '--input', '-i', nargs=1,
                        default=['data/default'],
                        help=docs.data_root)
    parser.add_argument('--model-root', '--model-dir', '-m', nargs=1,
                        default=['models/tmp'],
                        help=docs.model_root)
    parser.add_argument('--overwrite', '-f', action='store_true',
                        help=docs.overwrite)
    parser.add_argument('--deep', action='store_true',
                        help=docs.deep)
    parser.add_argument('--figs-dir', '--figures', nargs=1,
                        default=['figs'],
                        help=docs.figs_dir)

    # data settings
    parser.add_argument('--convert-mode', nargs='+', default=[0, 4], type=int,
                        help=docs.convert_mode)
    parser.add_argument('--transformation', '--augment', '-a', nargs='?',
                        default=None, const=0, type=int,
                        help=docs.transformation)
    parser.add_argument('--identity-prob', nargs=1, default=[0.01], type=float,
                        help=docs.identity_prob)
    parser.add_argument('--priority-mode', '--priority', nargs=1,
                        default=['random'], help=docs.priority_mode)
    parser.add_argument('--labeled', action='store_true', help=docs.labeled)

    # annotation settings
    parser.add_argument('--annotation-mode', '--annotate', nargs=1,
                        default=['disks'], help=docs.annotation_mode)
    parser.add_argument('--record-size', nargs=1, default=[10], type=int,
                        help=docs.record_size)
    parser.add_argument('--annotation-delay', nargs=1, default=[60], type=float,
                        help=docs.annotation_delay)

    # sizes relating to data
    parser.add_argument('--image-shape', '--shape', '-s', nargs=3, type=int,
                        default=[500, 500, 1], help=docs.image_shape)
    parser.add_argument('--data-size', '-N', nargs=1, default=[10000], type=int,
                        help=docs.data_size)
    parser.add_argument('--test-size', '-T', nargs=1, default=[1000], type=int,
                        help=docs.test_size)
    parser.add_argument('--batch-size', '-b', nargs=1, default=[16], type=int,
                        help=docs.batch_size)
    parser.add_argument('--num-objects', '-n', nargs=1, default=[40], type=int,
                        help=docs.num_objects)
    parser.add_argument('--pose-dim', '-p', nargs=1, default=[2], type=int,
                        help=docs.pose_dim)
    parser.add_argument('--num-shuffle', nargs=1, default=[1000], type=int,
                        help=docs.num_shuffle)

    # model architecture
    parser.add_argument('--base-shape', nargs='+', default=[28], type=int,
                        help=docs.base_shape)
    parser.add_argument('--level-filters', nargs='+', default=[128, 64, 32],
                        type=int, help=docs.level_filters)
    parser.add_argument('--level-depth', nargs='+', default=[2], type=int,
                        help=docs.level_depth)

    # sparse eval and other optimization settings
    parser.add_argument('--model', '-M', nargs='?', default='unet',
                        help=docs.model)
    parser.add_argument('--multiscale', action='store_true',
                        help=docs.multiscale)
    parser.add_argument('--use-var', action='store_true', help=docs.use_var)

    # model hyperparameters
    parser.add_argument('--dropout', nargs=1, default=[0.5], type=float,
                        help=docs.dropout)
    parser.add_argument('--initial-epoch', nargs=1, default=[0], type=int,
                        help=docs.initial_epoch)  # todo: get from ckpt
    parser.add_argument('--epochs', '-e', nargs=1, default=[1], type=int,
                        help=docs.epochs)
    parser.add_argument('--learning-rate', '-l', nargs=1, default=[0.1],
                        type=float, help=docs.learning_rate)
    parser.add_argument('--tol', nargs=1, default=[0.1], type=float,
                        help=docs.tol)

    # runtime settings
    parser.add_argument('--num-parallel-calls', '--cores', nargs=1, default=[-1],
                        type=int, help=docs.num_parallel_calls)
    parser.add_argument('--verbose', '-v', nargs='?', const=1, default=2,
                        type=int, help=docs.verbose)
    parser.add_argument('--keras-verbose', nargs='?', const=2, default=1,
                        type=int, help=docs.keras_verbose)
    parser.add_argument('--patient', action='store_true', help=docs.patient)
    parser.add_argument('--show', action='store_true', help=docs.show)
    parser.add_argument('--cache', action='store_true', help=docs.cache)
    parser.add_argument('--seconds', '--time', '--reload', '-t', '-r', nargs='?',
                        default=0, const=-1, type=int, help=docs.seconds)

    # visualize testing result
    parser.add_argument('--vis-number', nargs=1, default=0, type=int)

    args = parser.parse_args()
    art = Artifice(commands=args.commands,
                    convert_mode=args.convert_mode,
                    transformation=args.transformation,
                    identity_prob=args.identity_prob[0],
                    priority_mode=args.priority_mode[0],
                    labeled=args.labeled,
                    annotation_mode=args.annotation_mode[0],
                    record_size=args.record_size[0],
                    annotation_delay=args.annotation_delay[0],
                    data_root=args.data_root[0],
                    model_root=args.model_root[0],
                    overwrite=args.overwrite,
                    deep=args.deep,
                    figs_dir=args.figs_dir[0],
                    image_shape=args.image_shape,
                    data_size=args.data_size[0],
                    test_size=args.test_size[0],
                    batch_size=args.batch_size[0],
                    num_objects=args.num_objects[0],
                    pose_dim=args.pose_dim[0],
                    num_shuffle=args.num_shuffle[0],
                    base_shape=args.base_shape,
                    level_filters=args.level_filters,
                    level_depth=args.level_depth[0],
                    model=args.model,
                    multiscale=args.multiscale,
                    use_var=args.use_var,
                    dropout=args.dropout[0],
                    initial_epoch=args.initial_epoch[0],
                    epochs=args.epochs[0],
                    learning_rate=args.learning_rate[0],
                    tol=args.tol[0],
                    num_parallel_calls=args.num_parallel_calls[0],
                    verbose=args.verbose,
                    keras_verbose=args.keras_verbose,
                    eager=(not args.patient),
                    show=args.show,
                    cache=args.cache,
                    seconds=args.seconds,
                    vis_number=args.vis_number)
    logger.info(art)
    art()


if __name__ == "__main__":
  main()
