"""

    KeepNote
    Export HTML Extension

"""

#
#  KeepNote
#  Copyright (c) 2008-2009 Matt Rasmussen
#  Author: Matt Rasmussen <rasmus@mit.edu>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301, USA.
#


# python imports
import gettext
import os
import sys
import time
import shutil
import urllib
import xml.dom
from xml.dom import minidom

_ = gettext.gettext


# pygtk imports
import pygtk
pygtk.require('2.0')
from gtk import gdk
import gtk.glade
import gobject

# keepnote imports
import keepnote
from keepnote.notebook import NoteBookError, get_valid_unique_filename
from keepnote import notebook as notebooklib
from keepnote import tasklib
from keepnote import tarfile

# pygtk imports
try:
    import pygtk
    pygtk.require('2.0')
    from gtk import gdk
    import gtk.glade
    import gobject
except ImportError:
    # do not fail on gtk import error,
    # extension should be usable for non-graphical uses
    pass



class Extension (keepnote.Extension):
    
    version = (1, 0)
    name = "Export HTML"
    description = "Exports a notebook to HTML format"


    def __init__(self, app):
        """Initialize extension"""
        
        keepnote.Extension.__init__(self, app)
        self.app = app


    def on_new_window(self, window):
        """Initialize extension for a particular window"""

        # add menu options

        window.actiongroup.add_actions([
            ("Export HTML", None, "_HTML...",
             "", None,
             lambda w: self.on_export_notebook(window,
                                               window.get_notebook())),
            ])
        
        window.uimanager.add_ui_from_string(
            """
            <ui>
            <menubar name="main_menu_bar">
               <menu action="File">
                  <menu action="Export">
                     <menuitem action="Export HTML"/>
                  </menu>
               </menu>
            </menubar>
            </ui>
            """)


    def on_export_notebook(self, window, notebook):
        """Callback from gui for exporting a notebook"""
        
        dialog = gtk.FileChooserDialog("Export Notebook", window, 
            action=gtk.FILE_CHOOSER_ACTION_SAVE,
            buttons=("Cancel", gtk.RESPONSE_CANCEL,
                     "Export", gtk.RESPONSE_OK))


        filename = notebooklib.get_unique_filename(
            self.app.pref.archive_notebook_path,
            time.strftime(os.path.basename(window.notebook.get_path()) +
                          "-%Y-%m-%d"),
            "",
            ".")
        dialog.set_current_name(os.path.basename(filename))
        dialog.set_current_folder(self.app.pref.archive_notebook_path)
        
        response = dialog.run()

        self.app.pref.archive_notebook_path = dialog.get_current_folder()
        self.app.pref.changed.notify()


        if response == gtk.RESPONSE_OK:
            filename = dialog.get_filename()
            dialog.destroy()

            self.export_notebook(notebook, filename, window=window)


    def export_notebook(self, notebook, filename, window=None):
        
        if notebook is None:
            return

        if window:

            task = tasklib.Task(lambda task:
                                export_notebook(notebook, filename, task))

            window.wait_dialog("Exporting to '%s'..." %
                               os.path.basename(filename),
                               "Beginning export...",
                               task)

            # check exceptions
            try:
                ty, error, tracebk = task.exc_info()
                if error:
                    raise error
                window.set_status("Notebook exported")
                return True

            except NoteBookError, e:
                window.set_status("")
                window.error("Error while exporting notebook:\n%s" % e.msg, e,
                             tracebk)
                return False

            except Exception, e:
                window.set_status("")
                window.error("unknown error", e, tracebk)
                return False

        else:
            
            export_notebook(notebook, filename, None)


def truncate_filename(filename, maxsize=100):
    if len(filename) > maxsize:
        filename = "..." + filename[-(maxsize-3):]
    return filename


def relpath(path, start):
    head, tail = path, None
    head2, tail2 = start, None

    rel = []
    rel2 = []

    while head != head2 and (tail != "" or tail2 != ""):
        if len(head) > len(head2):
            head, tail = os.path.split(head)
            rel.append(tail)
        else:
            head2, tail2 = os.path.split(head2)
            rel2.append(u"..")

    rel2.extend(reversed(rel))
    return u"/".join(rel2)
        



def translate_links(notebook, path, node):

    def walk(node):

        if node.nodeType == node.ELEMENT_NODE and node.tagName == "a":
            url = node.getAttribute("href")
            if notebooklib.is_node_url(url):
                host, nodeid = notebooklib.parse_node_url(url)
                note = notebook.get_node_by_id(nodeid)
                if note:
                    newpath = u"/".join((relpath(note.get_path(), path), 
                                         u"page.html"))
                    node.setAttribute("href", urllib.quote(newpath))

        
        # recurse
        for child in node.childNodes:
            walk(child)

    walk(node)


def write_index(node, filename):
    
    out = file(filename, "wb")
    out.write((u"""<html>
<head><title>%s</title></head>
<body>
<frameset rows="75%, *" cols="*, 40%">
  <frame src="tree.html">
  <frame src="">
</frameset>
</body>
</html>
""") % node.get_title())
    out.close()


def export_notebook(notebook, filename, task):
    """Export notebook to HTML

       filename -- filename of export to create
    """

    if task is None:
        # create dummy task if needed
        task = tasklib.Task()

    if os.path.exists(filename):
        raise NoteBookError("File '%s' already exists" % filename)

    # make sure all modifications are saved first
    try:
        notebook.save()
    except Exception, e:
        raise NoteBookError("Could not save notebook before archiving", e)


    # first count # of files
    nnodes = [0]
    def walk(node):
        nnodes[0] += 1
        for child in node.get_children():
            walk(child)
    walk(notebook)

    task.set_message(("text", "Exporting %d notes..." % nnodes[0]))
    nnodes2 = [0]


    def export_page(node, path, arcname):

        filename = os.path.join(path, "page.html")
        filename2 = os.path.join(arcname, "page.html")
        
        try:
            dom = minidom.parse(filename)
                        
        except Exception, e:
            # error parsing file, use simple file export
            export_files(filename, filename2)
            
        else:
            translate_links(notebook, path, dom.documentElement)

            # TODO: ensure encoding issues handled

            # avoid writing <?xml> header 
            # (provides compatiability with browsers)
            out = open(filename2, "wb")
            dom.doctype.writexml(out)
            dom.documentElement.writexml(out)
            out.close()



    def export_node(node, path, arcname, index=False):

        # look for aborted export
        if task.aborted():
            raise NoteBookError("Backup canceled")

        # report progresss
        nnodes2[0] += 1
        task.set_message(("detail", truncate_filename(path)))
        task.set_percent(nnodes2[0] / float(nnodes[0]))

        skipfiles = set(child.get_basename()
                        for child in node.get_children())

        # make node directory
        os.mkdir(arcname)

        if index:
            write_index(node, os.path.join(arcname, "index.html"))


        if node.get_attr("content_type") == "text/xhtml+xml":
            skipfiles.add("page.html")
            # export xhtml
            export_page(node, path, arcname)

        # recurse files
        for f in os.listdir(path):
            if not os.path.islink(f) and f not in skipfiles:
                export_files(os.path.join(path, f),
                             os.path.join(arcname, f))

        # recurse nodes
        for child in node.get_children():
            f = child.get_basename()
            export_node(child,
                        os.path.join(path, f),
                        os.path.join(arcname, f))

    def export_files(path, arcname):
        # look for aborted export
        if task.aborted():
            raise NoteBookError("Backup canceled")
        
        if os.path.isfile(path):
            # copy files            
            shutil.copy(path, arcname)

        if os.path.isdir(path):            
            # export directory
            os.mkdir(arcname)

            # recurse
            for f in os.listdir(path):
                if not os.path.islink(f):
                    export_files(os.path.join(path, f),
                                 os.path.join(arcname, f))
    
    export_node(notebook, notebook.get_path(), filename, True)

    task.set_message(("text", "Closing export..."))
    task.set_message(("detail", ""))
    
    if task:
        task.finish()





