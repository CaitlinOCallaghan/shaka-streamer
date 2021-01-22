# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A module that feeds information from two named pipes into shaka-packager."""

import os
import subprocess

from . import input_configuration
from . import node_base
from . import pipeline_configuration

from streamer.output_stream import OutputStream
from streamer.pipeline_configuration import PipelineConfig
from streamer.util import is_url
from typing import List, Optional

# Alias a few classes to avoid repeating namespaces later.
MediaType = input_configuration.MediaType

ManifestFormat = pipeline_configuration.ManifestFormat
StreamingMode = pipeline_configuration.StreamingMode


INIT_SEGMENT = {
  MediaType.AUDIO: 'audio_{language}_{channels}c_{bitrate}_{codec}_init.{format}',
  MediaType.VIDEO: 'video_{resolution_name}_{bitrate}_{codec}_init.{format}',
  MediaType.TEXT: 'text_{language}_init.{format}',
}

MEDIA_SEGMENT = {
  MediaType.AUDIO: 'audio_{language}_{channels}c_{bitrate}_{codec}_$Number$.{format}',
  MediaType.VIDEO: 'video_{resolution_name}_{bitrate}_{codec}_$Number$.{format}',
  MediaType.TEXT: 'text_{language}_$Number$.{format}',
}

SINGLE_SEGMENT = {
  MediaType.AUDIO: 'audio_{language}_{channels}c_{bitrate}_{codec}.{format}',
  MediaType.VIDEO: 'video_{resolution_name}_{bitrate}_{codec}.{format}',
  MediaType.TEXT: 'text_{language}.{format}',
}


class SegmentError(Exception):
  """Raise when segment is incompatible with format."""
  pass

def build_path(output_dir, sub_path):
  """Handle annoying edge cases with paths for cloud upload.

  If a path has two slashes, GCS will create an intermediate directory named "".
  So we have to be careful in how we construct paths to avoid this.
  """
  # ControllerNode should have already stripped trailing slashes from the output
  # location.

  # Sometimes the segment dir is empty.  This handles that special case.
  if not sub_path:
    return output_dir

  if is_url(output_dir):
    # Don't use os.path.join, since URLs must use forward slashes and Streamer
    # could be used on Windows.
    return output_dir + '/' + sub_path

  return os.path.join(output_dir, sub_path)

def build_path(output_dir, sub_path):
  """Handle annoying edge cases with paths for cloud upload.

  If a path has two slashes, GCS will create an intermediate directory named "".
  So we have to be careful in how we construct paths to avoid this.
  """
  # ControllerNode should have already stripped trailing slashes from the output
  # location.

  # Sometimes the segment dir is empty.  This handles that special case.
  if not sub_path:
    return output_dir

  if is_url(output_dir):
    # Don't use os.path.join, since URLs must use forward slashes and Streamer
    # could be used on Windows.
    return output_dir + '/' + sub_path

  return os.path.join(output_dir, sub_path)


class PackagerNode(node_base.PolitelyWaitOnFinish):

  def __init__(self,
               pipeline_config: PipelineConfig,
               output_dir: str,
               output_streams: List[OutputStream],
               oauth: Optional[str],
               url_parameters: Optional[str]) -> None:
    super().__init__()
    self._pipeline_config: PipelineConfig = pipeline_config
    self._output_dir: str = output_dir
    self._segment_dir: str = build_path(
        output_dir, pipeline_config.segment_folder)
    self._output_streams: List[OutputStream] = output_streams
    self._oauth: Optional[str] = oauth
    self._url_parameters: Optional[str] = url_parameters

  def start(self) -> None:
    args = [
        'packager',
    ]

    if self._pipeline_config.skip_transcoding:
      args += [
          '--io_block_size', '65536',
      ]
      args += [self._setup_stream(stream) for stream in self._output_streams]
      args += self._setup_manifest_format()
    
    if self._pipeline_config.skip_transcoding == False:
      args += [self._setup_stream(stream) for stream in self._output_streams]

      if self._pipeline_config.quiet:
        args += [
            '--quiet',  # Only output error logs
        ]

      args += [
          # Segment duration given in seconds.
          '--segment_duration', str(self._pipeline_config.segment_size),
      ]

      if self._pipeline_config.streaming_mode == StreamingMode.LIVE:
        args += [
            # Number of seconds the user can rewind through backwards.
            '--time_shift_buffer_depth',
            str(self._pipeline_config.availability_window),
            # Number of segments preserved outside the current live window.
            # NOTE: This must not be set below 3, or the first segment in an HLS
            # playlist may become unavailable before the playlist is updated.
            '--preserved_segments_outside_live_window', '3',
            # Number of seconds of content encoded/packaged that is ahead of the
            # live edge.
            '--suggested_presentation_delay',
            str(self._pipeline_config.presentation_delay),
            # Number of seconds between manifest updates.
            '--minimum_update_period',
            str(self._pipeline_config.update_period),
        ]

      if is_url(self._output_dir):
        headers = [
          'Cache-Control: no-store, no-transform'
        ]

        if self._oauth:
          headers += ['Authorization: Bearer ' + self._oauth]

        args += ['--http_upload_headers', '\n'.join(headers)]

        if self._url_parameters:
          args += ['--http_upload_parameters', self._url_parameters]

        args += self._setup_manifest_format()

    if self._pipeline_config.encryption.enable:
      args += self._setup_encryption()

    stdout = None
    if self._pipeline_config.debug_logs:
      # Log by writing all Packager output to a file.  Unlike the logging
      # system in ffmpeg, this will stop any Packager output from getting to
      # the screen.
      stdout = open('PackagerNode.log', 'w')

    self._process: subprocess.Popen = self._create_process(
        args,
        stderr=subprocess.STDOUT,
        stdout=stdout)

  def _setup_stream(self, stream: OutputStream) -> str:
    dict = {
        # If pipe is None, this wasn't transcoded, so we take the input path
        # directly.
        'in': stream.pipe or stream.input.name,
        'stream': stream.type.value,
    }

    # Note: Shaka Packager will not accept 'und' as a language, but Shaka
    # Player will fill that in if the language metadata is missing from the
    # manifest/playlist.
    if stream.input.language and stream.input.language != 'und':
      dict['language'] = stream.input.language

    if self._pipeline_config.segment_per_file:
      dict['init_segment'] = build_path(
          self._segment_dir, stream.fill_template(INIT_SEGMENT[stream.type]))
      dict['segment_template'] = build_path(
          self._segment_dir, stream.fill_template(MEDIA_SEGMENT[stream.type]))
    else:
      dict['output'] = build_path(
          self._segment_dir, stream.fill_template(SINGLE_SEGMENT[stream.type]))

    # The format of this argument to Shaka Packager is a single string of
    # key=value pairs separated by commas.
    return ','.join(key + '=' + value for key, value in dict.items())

  def _setup_manifest_format(self) -> List[str]:
    args: List[str] = []
    if ManifestFormat.DASH in self._pipeline_config.manifest_format:
      if self._pipeline_config.streaming_mode == StreamingMode.VOD:
        args += [
            '--generate_static_live_mpd',
        ]
      args += [
          # Generate DASH manifest file.
          '--mpd_output',
          os.path.join(self._output_dir, self._pipeline_config.dash_output),
      ]
    if ManifestFormat.HLS in self._pipeline_config.manifest_format:
      if self._pipeline_config.streaming_mode == StreamingMode.LIVE:
        args += [
            '--hls_playlist_type', 'LIVE',
        ]
      else:
        args += [
            '--hls_playlist_type', 'VOD',
        ]
      args += [
          # Generate HLS playlist file(s).
          '--hls_master_playlist_output',
          os.path.join(self._output_dir, self._pipeline_config.hls_output),
      ]
    return args

  def _setup_encryption(self) -> List[str]:
    # Sets up encryption of content.
    args = [
      '--enable_widevine_encryption',
      '--key_server_url', self._pipeline_config.encryption.key_server_url,
      '--content_id', self._pipeline_config.encryption.content_id,
      '--signer', self._pipeline_config.encryption.signer,
      '--aes_signing_key', self._pipeline_config.encryption.signing_key,
      '--aes_signing_iv', self._pipeline_config.encryption.signing_iv,
      '--protection_scheme',
      self._pipeline_config.encryption.protection_scheme.value,
      '--clear_lead', str(self._pipeline_config.encryption.clear_lead),
    ]
    return args
