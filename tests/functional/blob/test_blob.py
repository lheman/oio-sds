# -*- coding: utf-8 -*-

# Copyright (C) 2015-2018 OpenIO SAS, as part of OpenIO SDS
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 3.0 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library.

import string
from os.path import isfile
from hashlib import md5
from urlparse import urlparse
from urllib import unquote
from oio.common.http import headers_from_object_metadata
from oio.common.http_eventlet import http_connect
from oio.common.constants import OIO_VERSION, CHUNK_HEADERS
from oio.common.fullpath import encode_fullpath
from oio.common.utils import cid_from_name
from oio.blob.utils import read_chunk_metadata
from tests.utils import CommonTestCase, random_id
from tests.functional.blob import random_buffer, \
    random_chunk_id


map_cfg = {'addr': 'Listen',
           'ns': 'grid_namespace',
           'basedir': 'grid_docroot'}


def _write_config(path, config):
    with open(path, 'w') as f:
        for k, v in config.iteritems():
            f.write("{0} {1}\n".format(map_cfg[k], config[k]))


# TODO we should the content of events sent by the rawx
class RawxAbstractTestSuite(object):

    def _chunk_attr(self, chunk_id, data, path=None):
        if path is not None:
            self.content_path = path
        headers = headers_from_object_metadata({
            'id': self.content_id,
            'version': self.content_version,
            'content_path': self.content_path,
            'chunk_method':
                'ec/algo=liberasurecode_rs_vand,k=6,m=3',
            'policy': 'TESTPOLICY',
            'container_id': self.cid,
            'chunk_hash': md5(data).hexdigest(),
            'full_path': self.fullpath,
            'oio_version': OIO_VERSION
        })
        headers[CHUNK_HEADERS['chunk_pos']] = 0
        headers[CHUNK_HEADERS['chunk_id']] = chunk_id
        headers[CHUNK_HEADERS['chunk_size']] = str(len(data))
        return headers

    def _rawx_url(self, chunkid):
        return '/'.join((self.rawx, chunkid))

    def _chunk_path(self, chunkid):
        chunkid = chunkid.upper()
        return self.rawx_path + '/' + chunkid[:3] + '/' + chunkid

    def _setup(self):
        self.container = 'blob'
        self.cid = cid_from_name(self.account, 'blob')
        self.content_path = 'test-plop'
        self.content_version = '1456938361143740'
        self.content_id = '0123456789ABCDEF'
        self.fullpath = encode_fullpath(
            self.account, 'blob', self.content_path, self.content_version,
            self.content_id)

    def _teardown(self):
        pass

    def _http_request(self, chunkurl, method, body, headers, trailers=None):
        parsed = urlparse(chunkurl)
        if method == 'PUT':
            headers['transfer-encoding'] = 'chunked'
        if trailers:
            headers['Trailer'] = list()
            for k, v in trailers.iteritems():
                headers['Trailer'].append(k)

        conn = http_connect(parsed.netloc, method, parsed.path,
                            headers)
        if method == 'PUT':
            if body:
                conn.send('%x\r\n%s\r\n' % (len(body), body))
            conn.send('0\r\n')
            if trailers:
                for k, v in trailers.iteritems():
                    conn.send('%s: %s\r\n' % (k, v))
            conn.send('\r\n')
        if method == 'PUT':
            del headers['transfer-encoding']
        if trailers:
            del headers['Trailer']

        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def test_copy_errors(self):
        length = 100
        chunkid = random_chunk_id()
        chunkdata = random_buffer(string.printable, length)
        chunkurl = self._rawx_url(chunkid)
        self._check_not_present(chunkurl)
        headers = self._chunk_attr(chunkid, chunkdata)
        metachunk_size = 9 * length
        # TODO take random legit value
        metachunk_hash = md5().hexdigest()
        # TODO should also include meta-chunk-hash
        trailers = {'x-oio-chunk-meta-metachunk-size': metachunk_size,
                    'x-oio-chunk-meta-metachunk-hash': metachunk_hash}
        # Initial put that must succeed
        resp, body = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                        trailers)
        self.assertEqual(201, resp.status)
        self.assertEqual(headers['x-oio-chunk-meta-chunk-hash'].upper(),
                         resp.getheader('x-oio-chunk-meta-chunk-hash'))
        self.assertEqual(headers['x-oio-chunk-meta-chunk-size'],
                         resp.getheader('x-oio-chunk-meta-chunk-size'))
        copyid = random_chunk_id()
        copyid = chunkid[:-60] + copyid[-60:]
        copyurl = self._rawx_url(copyid)
        headers = {}
        headers["Destination"] = copyurl
        resp, _ = self._http_request(chunkurl, 'COPY', '', headers)
        self.assertEqual(400, resp.status)

        headers = {}
        headers["Destination"] = chunkurl
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
                "account-snapshot", "container-snapshot", "test"+"-snapshot",
                1456938361143741, random_id(32))
        resp, _ = self._http_request(chunkurl, 'COPY', '', headers)
        self.assertEqual(403, resp.status)

        headers = {}
        resp, _ = self._http_request(chunkurl, 'COPY', '', headers)
        self.assertEqual(400, resp.status)

    def _cycle_put(self, length, expected, remove_headers=None, path=None,
                   chunkid_lowercase=False):
        if path:
            self.path = path
        chunkid = random_chunk_id()
        chunkdata = random_buffer(string.printable, length)
        if chunkid_lowercase:
            chunkurl = self._rawx_url(chunkid.lower())
        else:
            chunkurl = self._rawx_url(chunkid)
        chunkpath = self._chunk_path(chunkid)
        headers = self._chunk_attr(chunkid, chunkdata)
        fullpath = headers['x-oio-chunk-meta-full-path']
        if remove_headers:
            for h in remove_headers:
                del headers[h]

        # we do not really care about the actual value
        metachunk_size = 9 * length
        # TODO take random legit value
        metachunk_hash = md5().hexdigest()
        # TODO should also include meta-chunk-hash
        trailers = {'x-oio-chunk-meta-metachunk-size': metachunk_size,
                    'x-oio-chunk-meta-metachunk-hash': metachunk_hash}

        self._check_not_present(chunkurl)

        # Initial put that must succeed
        resp, body = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                        trailers)
        self.assertEqual(expected, resp.status)
        if expected / 100 != 2:
            self.assertFalse(isfile(chunkpath))
            return
        chunk_hash = headers.get('x-oio-chunk-meta-chunk-hash',
                                 md5(chunkdata).hexdigest()).upper()
        chunk_size = headers.get('x-oio-chunk-meta-chunk-size', str(length))
        self.assertEqual(chunk_hash,
                         resp.getheader('x-oio-chunk-meta-chunk-hash'))
        self.assertEqual(chunk_size,
                         resp.getheader('x-oio-chunk-meta-chunk-size'))

        # the first PUT succeeded, the second MUST fail
        resp, body = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                        trailers)
        self.assertEqual(409, resp.status)
        # check the file if is correct
        with open(chunkpath) as f:
            data = f.read()
            self.assertEqual(data, chunkdata)

        # check the whole download is correct
        # TODO FIXME getting an empty content should return 204
        resp, body = self._http_request(chunkurl, 'GET', '', {})
        self.assertEqual(200, resp.status)
        self.assertEqual(body, chunkdata)
        self.assertEqual(fullpath,
                         resp.getheader('x-oio-chunk-meta-full-path'))
        headers.pop('x-oio-chunk-meta-full-path')

        headers['x-oio-chunk-meta-metachunk-size'] = metachunk_size
        headers['x-oio-chunk-meta-metachunk-hash'] = metachunk_hash.upper()
        headers['x-oio-chunk-meta-chunk-hash'] = chunk_hash
        headers['x-oio-chunk-meta-chunk-size'] = chunk_size
        for k, v in headers.items():
            if k == 'x-oio-chunk-meta-content-path':
                self.assertEqual(unquote(resp.getheader(k)), unquote(str(v)))
            else:
                self.assertEqual(resp.getheader(k), str(v))

        # check ranges can be downloaded
        def ranges():
            if length <= 0:
                return
            yield (0, 0)
            if length > 1:
                yield (0, 1)
                yield (0, length-1)
                yield (length-2, length-1)
            if length > 4:
                yield (2, length-3)
        for start, end in ranges():
            r = "bytes={0}-{1}".format(start, end)
            resp, body = self._http_request(chunkurl, 'GET', '', {'Range': r})
            self.assertEqual(resp.status/100, 2)
            self.assertEqual(len(body), end-start+1)
            self.assertEqual(body, chunkdata[start:end+1])
        if length > 0:
            # TODO FIXME getting an unsatisfiable range on an empty content
            # returns "200 OK" with an empty body, but should return 416
            r = "bytes={0}-{1}".format(length, length+1)
            resp, body = self._http_request(chunkurl, 'GET', '', {'Range': r})
            self.assertEqual(416, resp.status)

        # delete the chunk, check it is missing as expected
        resp, body = self._http_request(chunkurl, 'DELETE', '', {})
        self.assertEqual(204, resp.status)
        self.assertFalse(isfile(chunkpath))

        self._check_not_present(chunkurl)

    def test_empty_chunk(self):
        self._cycle_put(0, 201)

    def test_small_chunks(self):
        for i in [1, 2, 3, 4, 32, 64, 128, 256, 512]:
            self._cycle_put(i, 201)

    def test_1K(self):
        self._cycle_put(1024, 201)

    def test_8K(self):
        self._cycle_put(8*1024, 201)

    def test_64K(self):
        self._cycle_put(64*1024, 201)

    def test_1M(self):
        self._cycle_put(1024*1024, 201)

    def test_fat_chunk(self):
        self._cycle_put(1024*1024*128, 201)

    def test_missing_headers(self):
        self._cycle_put(32, 400,
                        remove_headers=['x-oio-chunk-meta-chunk-pos'])
        self._cycle_put(32, 201,
                        remove_headers=['x-oio-chunk-meta-chunk-hash'])
        self._cycle_put(32, 201,
                        remove_headers=['x-oio-chunk-meta-chunk-size'])
        self._cycle_put(32, 201,
                        remove_headers=['x-oio-chunk-meta-chunk-id'])
        self._cycle_put(32, 400,
                        remove_headers=['x-oio-chunk-meta-full-path'])
        self._cycle_put(32, 201,
                        remove_headers=['x-oio-chunk-meta-content-id'])
        self._cycle_put(32, 201,
                        remove_headers=['x-oio-chunk-meta-content-version'])
        self._cycle_put(32, 201,
                        remove_headers=['x-oio-chunk-meta-content-path'])
        self._cycle_put(32, 201,
                        remove_headers=['x-oio-chunk-meta-container-id'])
        self._cycle_put(32, 201,
                        remove_headers=[
                            'x-oio-chunk-meta-container-id',
                            'x-oio-chunk-meta-content-path',
                            'x-oio-chunk-meta-content-version',
                            'x-oio-chunk-meta-content-id'])

    def _check_not_present(self, chunkurl):
        resp, body = self._http_request(chunkurl, 'GET', '', {})
        self.assertEqual(404, resp.status)
        resp, body = self._http_request(chunkurl, 'DELETE', '', {})
        self.assertEqual(404, resp.status)

    def _check_bad_headers(self, length, bad_headers=None, bad_trailers=None):
        chunkid = random_chunk_id()
        chunkdata = random_buffer(string.printable, length)
        chunkurl = self._rawx_url(chunkid)
        headers = self._chunk_attr(chunkid, chunkdata)
        # force the bad headers
        if bad_headers:
            for k, v in bad_headers.items():
                headers[k] = v
        trailers = None
        if bad_trailers:
            trailers = {}
            for k, v in bad_trailers.items():
                trailers[k] = v
                if headers.get(k, None):
                    del headers[k]

        self._check_not_present(chunkurl)

        resp, body = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                        trailers)
        self.assertEqual(400, resp.status)

        self._check_not_present(chunkurl)

    def test_bad_chunkhash(self):
        # not hexa
        self._check_bad_headers(
            32, bad_headers={'x-oio-chunk-meta-chunk-hash': '0'})
        self._check_bad_headers(
            32, bad_headers={'x-oio-chunk-meta-chunk-hash': 'xx'})
        self._check_bad_headers(
            32, bad_headers={'x-oio-chunk-meta-chunk-hash':
                             'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'})

        self._check_bad_headers(
            32, bad_trailers={'x-oio-chunk-meta-chunk-hash': 'xx'})
        self._check_bad_headers(
            32, bad_trailers={'x-oio-chunk-meta-chunk-hash': 'xx'})
        self._check_bad_headers(
            32, bad_trailers={'x-oio-chunk-meta-chunk-hash':
                              'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'})

    def test_bad_chunkid(self):
        self._check_bad_headers(
            32, bad_headers={'x-oio-chunk-meta-chunk-id': '00'*32})

    def test_chunkid_lowercase(self):
        self._cycle_put(32, 201, chunkid_lowercase=True)

    def _cycle_copy(self, path):
        if path:
            self.path = path
        chunkid = random_chunk_id()
        chunkdata = random_buffer(string.printable, 1)
        chunkurl = self._rawx_url(chunkid)
        chunkpath = self._chunk_path(chunkid)
        headers1 = self._chunk_attr(chunkid, chunkdata)
        metachunk_hash = md5().hexdigest()
        trailers = {'x-oio-chunk-meta-metachunk-size': 1,
                    'x-oio-chunk-meta-metachunk-hash': metachunk_hash}

        self._check_not_present(chunkurl)
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers1,
                                     trailers)
        self.assertEqual(201, resp.status)
        self.assertEqual(headers1['x-oio-chunk-meta-chunk-hash'].upper(),
                         resp.getheader('x-oio-chunk-meta-chunk-hash'))
        self.assertEqual(headers1['x-oio-chunk-meta-chunk-size'],
                         resp.getheader('x-oio-chunk-meta-chunk-size'))

        copyid = random_chunk_id()
        copyid = chunkid[:-60] + copyid[-60:]
        copyurl = self._rawx_url(copyid)
        copypath = self._chunk_path(copyid)

        headers2 = {}
        headers2["Destination"] = copyurl
        copy_account = "account-snapshot"
        copy_container = "container-snapshot"
        copy_container_id = cid_from_name(copy_account, copy_container)
        copy_path = path+"-snapshot"
        copy_version = 1456938361143741
        copy_id = random_id(32)
        copy_fullpath = encode_fullpath(
            copy_account, copy_container, copy_path, copy_version, copy_id)
        headers2['x-oio-chunk-meta-full-path'] = copy_fullpath
        resp, _ = self._http_request(chunkurl, 'COPY', '', headers2)
        self.assertEqual(201, resp.status)

        resp, body = self._http_request(chunkurl, 'GET', '', {})
        self.assertEqual(200, resp.status)
        headers1['x-oio-chunk-meta-chunk-hash'] = \
            headers1['x-oio-chunk-meta-chunk-hash'].upper()
        for k, v in headers1.items():
            if k == 'x-oio-chunk-meta-content-path':
                self.assertEqual(unquote(resp.getheader(k)), unquote(str(v)))
            else:
                self.assertEqual(resp.getheader(k), str(v))

        resp, body = self._http_request(copyurl, 'GET', '', {})
        self.assertEqual(200, resp.status)
        headers2_bis = headers1.copy()
        headers2_bis['x-oio-chunk-meta-full-path'] = \
            headers2['x-oio-chunk-meta-full-path']
        headers2_bis['x-oio-chunk-meta-content-path'] = copy_path
        headers2_bis['x-oio-chunk-meta-content-version'] = copy_version
        headers2_bis['x-oio-chunk-meta-content-id'] = copy_id
        headers2_bis['x-oio-chunk-meta-container-id'] = copy_container_id
        headers2_bis['x-oio-chunk-meta-chunk-id'] = copyid
        for k, v in headers2_bis.items():
            if k == 'x-oio-chunk-meta-content-path':
                self.assertEqual(unquote(resp.getheader(k)), unquote(str(v)))
            else:
                self.assertEqual(resp.getheader(k), str(v))

        with open(chunkpath, 'r') as fd:
            meta, _ = read_chunk_metadata(fd, chunkid)
            self.assertEqual(headers1['x-oio-chunk-meta-full-path'],
                             meta['full_path'])
            self.assertEqual(1, len(meta['links']))
            self.assertEqual(headers2['x-oio-chunk-meta-full-path'],
                             meta['links'][copyid])

        with open(copypath, 'r') as fd:
            meta, _ = read_chunk_metadata(fd, copyid)
            self.assertEqual(headers2['x-oio-chunk-meta-full-path'],
                             meta['full_path'])
            self.assertEqual(1, len(meta['links']))
            self.assertEqual(headers1['x-oio-chunk-meta-full-path'],
                             meta['links'][chunkid])

        resp, body = self._http_request(chunkurl, 'DELETE', '', {})
        self.assertEqual(204, resp.status)
        resp, body = self._http_request(chunkurl, 'GET', '', {})
        self.assertEqual(404, resp.status)

        resp, body = self._http_request(copyurl, 'GET', '', {})
        self.assertEqual(200, resp.status)
        self.assertEqual(headers2['x-oio-chunk-meta-full-path'],
                         resp.getheader('x-oio-chunk-meta-full-path'))

        with open(copypath, 'r') as fd:
            meta, _ = read_chunk_metadata(fd, copyid)
            self.assertEqual(headers2['x-oio-chunk-meta-full-path'],
                             meta['full_path'])
            self.assertEqual(0, len(meta['links']))

        resp, body = self._http_request(copyurl, 'DELETE', '', {})
        self.assertEqual(204, resp.status)
        resp, body = self._http_request(copyurl, 'GET', '', {})
        self.assertEqual(404, resp.status)

    def test_strange_path(self):
        strange_paths = [
                "Annual report.txt",
                "foo+bar=foobar.txt",
                "100%_bug_free.c",
                "forward/slash/allowed",
                "I\\put\\backslashes\\and$dollar$signs$in$file$names",
                "Je suis tombé sur la tête, mais ça va bien.",
                "%s%f%u%d%%",
                "{1},{0},{3}",
                "carriage\rreturn",
                "line\nfeed",
                "ta\tbu\tla\ttion",
                "controlchars",
                "//azeaze\\//azeaz\\//azea"
                ]
        for path in strange_paths:
            self._cycle_put(1, 201, path=path)
            self._cycle_copy(path)

    def test_copy_with_same_chunkid(self):
        metachunk_hash = md5().hexdigest()
        trailers = {'x-oio-chunk-meta-metachunk-size': 1,
                    'x-oio-chunk-meta-metachunk-hash': metachunk_hash}

        chunkid1 = random_chunk_id()
        chunkdata1 = random_buffer(string.printable, 1)
        chunkurl1 = self._rawx_url(chunkid1)
        headers1 = self._chunk_attr(chunkid1, chunkdata1)
        self._check_not_present(chunkurl1)
        resp, _ = self._http_request(chunkurl1, 'PUT', chunkdata1, headers1,
                                     trailers)
        self.assertEqual(201, resp.status)
        self.assertEqual(headers1['x-oio-chunk-meta-chunk-hash'].upper(),
                         resp.getheader('x-oio-chunk-meta-chunk-hash'))
        self.assertEqual(headers1['x-oio-chunk-meta-chunk-size'].upper(),
                         resp.getheader('x-oio-chunk-meta-chunk-size'))

        headers = {}
        headers["Destination"] = chunkurl1
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
                "account-snapshot", "container-snapshot", "content-snapshot",
                1456938361143741, random_id(32))
        resp, _ = self._http_request(chunkurl1, 'COPY', '', headers)
        self.assertEqual(403, resp.status)

    def test_copy_with_existing_destination(self):
        metachunk_hash = md5().hexdigest()
        trailers = {'x-oio-chunk-meta-metachunk-size': 1,
                    'x-oio-chunk-meta-metachunk-hash': metachunk_hash}

        chunkid1 = random_chunk_id()
        chunkdata1 = random_buffer(string.printable, 1)
        chunkurl1 = self._rawx_url(chunkid1)
        headers1 = self._chunk_attr(chunkid1, chunkdata1)
        self._check_not_present(chunkurl1)
        resp, _ = self._http_request(chunkurl1, 'PUT', chunkdata1, headers1,
                                     trailers)
        self.assertEqual(201, resp.status)
        self.assertEqual(headers1['x-oio-chunk-meta-chunk-hash'].upper(),
                         resp.getheader('x-oio-chunk-meta-chunk-hash'))
        self.assertEqual(headers1['x-oio-chunk-meta-chunk-size'],
                         resp.getheader('x-oio-chunk-meta-chunk-size'))

        chunkid2 = random_chunk_id()
        chunkdata2 = random_buffer(string.printable, 1)
        chunkurl2 = self._rawx_url(chunkid2)
        headers2 = self._chunk_attr(chunkid2, chunkdata2)
        self._check_not_present(chunkurl2)
        resp, _ = self._http_request(chunkurl2, 'PUT', chunkdata2, headers2,
                                     trailers)
        self.assertEqual(201, resp.status)
        self.assertEqual(headers2['x-oio-chunk-meta-chunk-hash'].upper(),
                         resp.getheader('x-oio-chunk-meta-chunk-hash'))
        self.assertEqual(headers2['x-oio-chunk-meta-chunk-size'],
                         resp.getheader('x-oio-chunk-meta-chunk-size'))

        headers = {}
        headers["Destination"] = chunkurl2
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
                "account-snapshot", "container-snapshot", "content-snapshot",
                1456938361143741, random_id(32))
        resp, _ = self._http_request(chunkurl1, 'COPY', '', headers)
        self.assertEqual(409, resp.status)

    def test_copy_with_nonexistent_source(self):
        metachunk_hash = md5().hexdigest()
        trailers = {'x-oio-chunk-meta-metachunk-size': 1,
                    'x-oio-chunk-meta-metachunk-hash': metachunk_hash}

        chunkid1 = random_chunk_id()
        chunkurl1 = self._rawx_url(chunkid1)

        chunkid2 = random_chunk_id()
        chunkdata2 = random_buffer(string.printable, 1)
        chunkurl2 = self._rawx_url(chunkid2)
        headers2 = self._chunk_attr(chunkid2, chunkdata2)
        self._check_not_present(chunkurl2)
        resp, _ = self._http_request(chunkurl2, 'PUT', chunkdata2, headers2,
                                     trailers)
        self.assertEqual(201, resp.status)
        self.assertEqual(headers2['x-oio-chunk-meta-chunk-hash'].upper(),
                         resp.getheader('x-oio-chunk-meta-chunk-hash'))
        self.assertEqual(headers2['x-oio-chunk-meta-chunk-size'],
                         resp.getheader('x-oio-chunk-meta-chunk-size'))

        headers = {}
        headers["Destination"] = chunkurl2
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
                "account-snapshot", "container-snapshot", "content-snapshot",
                1456938361143741, random_id(32))
        resp, _ = self._http_request(chunkurl1, 'COPY', '', headers)
        self.assertEqual(404, resp.status)

    def test_wrong_fullpath(self):
        metachunk_hash = md5().hexdigest()
        trailers = {'x-oio-chunk-meta-metachunk-size': 1,
                    'x-oio-chunk-meta-metachunk-hash': metachunk_hash}

        chunkid = random_chunk_id()
        chunkdata = random_buffer(string.printable, 1)
        chunkurl = self._rawx_url(chunkid)
        hdrs = self._chunk_attr(chunkid, chunkdata)
        self._check_not_present(chunkurl)

        headers = hdrs.copy()
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', self.content_path, self.content_version,
            self.content_id) + "/too_long"
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', self.content_path, self.content_version,
            self.content_id).rsplit('/', 2)[0]
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            'wrong-account', 'blob', self.content_path, self.content_version,
            self.content_id)
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'wrong-container', self.content_path,
            self.content_version, self.content_id)
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', 'wrong-path', self.content_version,
            self.content_id)
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', self.content_path, 9999999999999999,
            self.content_id)
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', self.content_path, self.content_version,
            '9999999999999999')
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        del headers['x-oio-chunk-meta-container-id']
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            'empty', 'blob', self.content_path, self.content_version,
            self.content_id).replace('empty', '')
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        del headers['x-oio-chunk-meta-container-id']
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'empty', self.content_path, self.content_version,
            self.content_id).replace('empty', '')
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        del headers['x-oio-chunk-meta-content-path']
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', 'empty', self.content_version,
            self.content_id).replace('empty', '')
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        del headers['x-oio-chunk-meta-content-version']
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', self.content_path, 'empty',
            self.content_id).replace('empty', '')
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        del headers['x-oio-chunk-meta-content-id']
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', self.content_path, self.content_version,
            'empty').replace('empty', '')
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        del headers['x-oio-chunk-meta-content-version']
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', self.content_path, 'digit', self.content_id)
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)

        headers = hdrs.copy()
        del headers['x-oio-chunk-meta-content-id']
        headers['x-oio-chunk-meta-full-path'] = encode_fullpath(
            self.account, 'blob', self.content_path, self.content_version,
            'hexa')
        resp, _ = self._http_request(chunkurl, 'PUT', chunkdata, headers,
                                     trailers)
        self.assertEqual(400, resp.status)


class RawxV2TestSuite(CommonTestCase, RawxAbstractTestSuite):

    def setUp(self):
        super(RawxV2TestSuite, self).setUp()
        self._setup()
        _, rawx_path, rawx_addr, _ = self.get_service_url('rawx')
        self.rawx = 'http://' + rawx_addr
        self.rawx_path = rawx_path + '/'

    def tearDown(self):
        super(RawxV2TestSuite, self).tearDown()
        self._teardown()
