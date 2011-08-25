"""TODO: module-level docstring."""
from theano import function, shared
import theano.tensor as T
import copy
from pylearn2.config import yaml_parse


class Monitor(object):
    """
    A class for monitoring Models while they are being trained.

    A monitor object records the number of minibatches and number of examples
    the model has trained, as well as any number of "channels" that track
    quantities of interest (examples: the objective function, measures of
    hidden unit activity, reconstruction error, sum of squared second
    derivatives,  etc.)
    """
    def __init__(self, model):
        """
        Makes a monitor for `model`. Assumes the model has not been
        trained at all yet.

        Parameters
        ----------
        model : object
            An object that implements the `Model` interface specified in
            `pylearn2.models`.
        """
        self.model = model
        self.channels = {}
        self.batches_seen = 0
        self.examples_seen = 0
        self.dataset = None
        self.dirty = True
        self.names_to_del = []

    def set_dataset(self, dataset, batches, batch_size):
        """
        Determines the data used to calculate the values of each channel.

        Parameters
        ----------
        dataset : object
            A `pylearn2.datasets.Dataset` object.
        batches : int
            Number of batches of examples to draw.
        batch_size : int
            The number of examples per batch.
        """
        # TODO: why is this not specifiable via the constructor? Is it
        # intended that you be able to switch datasets after using it for
        # a while?

        # TODO: maybe error checking; check dataset has the appropriate
        # attributes for use by the monitor. Check that batches and batch_size
        # work as indices.
        self.dataset = dataset
        self.batches = batches
        self.batch_size = batch_size

    def __call__(self):
        """
        Runs the model on the monitoring dataset in order to add one
        data point to each of the channels.
        """
        if self.dirty:
            self.redo_theano()

        model = self.model
        d = self.dataset

        if d:
            if isinstance(d, str):
                d = yaml_parse.load(d)
                self.dataset = d

            s = d.get_stream_position()

            d.restart_stream()

            self.begin_record_entry()

            for i in xrange(self.batches):
                X = d.get_batch_design(self.batch_size)
                self.accum(X)

            # TODO: use logging infrastructure so that user can configure
            # formatting
            print "Monitoring step:"
            print "\tBatches seen: %d" % self.batches_seen
            print "\tExamples seen: %d" % self.examples_seen
            for channel_name in self.channels:
                channel = self.channels[channel_name]
                channel.batch_record.append(self.batches_seen)
                channel.example_record.append(self.examples_seen)
                val = (channel.val_shared.get_value(borrow=False) /
                       float(self.batches))
                channel.val_record.append(val)
                # TODO: use logging infrastructure so that user can configure
                # formatting
                print "\t%s: %s" % (channel_name, str(val))

            d.set_stream_position(s)

    def redo_theano(self):
        """
        Recompiles Theano functions used by this monitor.

        This is needed so that if new channels are added, Theano's
        optimizations make sure (to the extent that they can) that the new
        channels and old channels don't have any redundant calculations.

        It is also needed to regenerate Theano functions after pickling and
        unpickling, since Theano functions should not be pickled.
        """
        self.dirty = False
        init_names = dir(self)
        updates = {}
        for channel in self.channels.values():
            updates[channel.val_shared] = 0.0
        self.begin_record_entry = function(inputs=[], updates=updates)
        updates = {}
        givens = {}
        X = T.matrix()
        for channel in self.channels.values():
            givens[channel.ipt] = X
            updates[channel.val_shared] = channel.val_shared + channel.val
        self.accum = function([X], givens=givens, updates=updates)
        final_names = dir(self)
        self.register_names_to_del([name for name in final_names
                                    if name not in init_names])

    def register_names_to_del(self, names):
        """
        Register names of fields that should be deleted before pickling.

        Parameters
        ----------
        names : list
            A list of attribute names as strings.
        """
        for name in names:
            if name not in self.names_to_del:
                self.names_to_del.append(name)

    def __getstate__(self):
        """
        In order to avoid pickling a copy of the dataset whenever a monitor
        is saved, the __getstate__ method replaces the dataset field with the
        dataset's yaml source. This is not a perfect solution because it won't
        work with job resuming, which would require saving the state of the
        dataset's random number generator.

        Like in the Model class, we also need to avoid saving any Theano
        functions, so we delete everything that can be regenerated with
        `redo_theano` by deleting the fields in `self.names_to_del`
        """
        temp = self.dataset
        if not isinstance(self.dataset, str):
            self.dataset = self.dataset.yaml_src
        d = copy.copy(self.__dict__)
        self.dataset = temp
        for name in self.names_to_del:
            if name in d:
                del d[name]
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)

    def add_channel(self, name, ipt, val):
        """
        Asks the monitor to start tracking a new value.  Can be run even
        after the monitor is already in use.

        Parameters
        ----------
        name: str
            The display name in the monitor.
        ipt: tensor_like
            The symbolic tensor which should be clamped to the data.
        val: tensor_like
            The value (function of `ipt`) to be tracked.
        """

        if name in self.channels:
            raise ValueError("Tried to create the same channel twice (%s)" %
                             name)
        self.channels[name] = Channel(ipt, val, name)
        self.dirty = True

    @classmethod
    def get_monitor(cls, model):
        """
        Returns a model's monitor. If the model doesn't have a monitor yet,
        installs one and returns that.

        Parameters
        ----------
        model : object
            An object that implements the `Model` interface specified in
            `pylearn2.models`.
        """
        if hasattr(model, 'monitor'):
            rval = model.monitor
        else:
            rval = Monitor(model)
            model.monitor = rval
        return rval


class Channel(object):
    """
    A class representing a specific quantity to be monitored.
    """
    def __init__(self, ipt, val, name):
        """
        Creates a channel for a quantity to be monitored.

        Parameters
        ----------
        name: str
            The display name in the monitor.
        ipt: tensor_like
            The symbolic tensor which should be clamped to the data.
        val: tensor_like
            The value (function of `ipt`) to be tracked.
        """
        self.ipt = ipt
        self.val = val
        self.val_shared = shared(0.0, name + "_tracker")
        self.batch_record = []
        self.example_record = []
        self.val_record = []
